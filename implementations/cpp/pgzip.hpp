// Parallel gzip writer: one gzip member compressed on several threads.
//
// Output is split into fixed-size blocks. Worker threads compress each block
// as raw deflate, primed with the previous 32 KiB as a dictionary and ended
// with a sync flush (the last block with Z_FINISH), so the blocks join into a
// single deflate stream (the pigz technique). The calling thread writes the
// blocks in order behind one gzip header and combines their CRC-32s.
#ifndef HASHFASTA_PGZIP_HPP
#define HASHFASTA_PGZIP_HPP

#include <unistd.h>
#include <zlib.h>

#include <algorithm>
#include <cerrno>
#include <condition_variable>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace hashfasta {

class ParallelGzip {
public:
    ParallelGzip(int fd, int level, int threads)
        : fd_(fd), level_(level), slots_(2 * static_cast<std::size_t>(threads)) {
        cur_.reserve(kBlock);
        // gzip header: deflate, no flags, mtime 0, no extra flags, OS = Unix
        static const unsigned char header[10] = {0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 3};
        write_all(header, sizeof header);
        for (int i = 0; i < threads; i++) workers_.emplace_back([this] { work(); });
    }
    ParallelGzip(const ParallelGzip &) = delete;
    ParallelGzip &operator=(const ParallelGzip &) = delete;
    ~ParallelGzip() { stop(); }

    void write(const void *data, std::size_t len) {
        const unsigned char *d = static_cast<const unsigned char *>(data);
        while (len) {
            std::size_t n = std::min(kBlock - cur_.size(), len);
            cur_.insert(cur_.end(), d, d + n);
            d += n;
            len -= n;
            if (cur_.size() == kBlock) submit(false);
        }
    }

    // Compress what remains, write the gzip trailer and stop the threads.
    void finish() {
        submit(true);
        while (written_ < submitted_) write_next();
        unsigned char trailer[8];
        for (int i = 0; i < 4; i++) {
            trailer[i] = static_cast<unsigned char>(crc_ >> (8 * i));
            trailer[4 + i] = static_cast<unsigned char>(total_ >> (8 * i));  // size mod 2^32
        }
        write_all(trailer, sizeof trailer);
        stop();
    }

private:
    static constexpr std::size_t kBlock = 128 * 1024;  // uncompressed bytes per job
    static constexpr std::size_t kDict = 32768;        // deflate window

    enum class State { Free, Queued, Done };
    struct Slot {
        std::vector<unsigned char> in, dict, out;
        unsigned long crc = 0;
        bool last = false, failed = false;
        State state = State::Free;
    };

    void write_all(const unsigned char *data, std::size_t len) {
        while (len) {
            ssize_t n = ::write(fd_, data, len);
            if (n < 0) {
                if (errno == EINTR) continue;
                throw std::runtime_error(std::string("write failed: ") + std::strerror(errno));
            }
            data += n;
            len -= static_cast<std::size_t>(n);
        }
    }

    void submit(bool last) {
        while (written_ + slots_.size() <= submitted_) write_next();
        Slot &s = slots_[submitted_ % slots_.size()];
        s.in.swap(cur_);
        cur_.clear();
        cur_.reserve(kBlock);
        s.dict.assign(tail_.begin(), tail_.end());
        s.last = last;
        // The last kDict bytes submitted so far prime the next job.
        tail_.insert(tail_.end(), s.in.end() - static_cast<std::ptrdiff_t>(std::min(s.in.size(), kDict)),
                     s.in.end());
        if (tail_.size() > kDict) tail_.erase(tail_.begin(), tail_.end() - kDict);
        std::lock_guard<std::mutex> lock(mutex_);
        s.state = State::Queued;
        submitted_++;
        work_.notify_one();
    }

    // Wait for the oldest unwritten job and write it out.
    void write_next() {
        Slot &s = slots_[written_ % slots_.size()];
        {
            std::unique_lock<std::mutex> lock(mutex_);
            done_.wait(lock, [&] { return s.state == State::Done; });
        }
        if (s.failed) throw std::runtime_error("write failed: zlib compression error");
        write_all(s.out.data(), s.out.size());
        crc_ = crc32_combine(crc_, s.crc, static_cast<z_off_t>(s.in.size()));
        total_ += s.in.size();
        s.state = State::Free;
        written_++;
    }

    void work() {
        z_stream z{};
        bool ok = deflateInit2(&z, level_, Z_DEFLATED, -15, 8, Z_DEFAULT_STRATEGY) == Z_OK;
        std::unique_lock<std::mutex> lock(mutex_);
        for (;;) {
            work_.wait(lock, [&] { return stopping_ || taken_ < submitted_; });
            if (stopping_) break;
            Slot &s = slots_[taken_++ % slots_.size()];
            lock.unlock();
            s.failed = !ok || !compress(z, s);
            lock.lock();
            s.state = State::Done;
            done_.notify_all();
        }
        if (ok) deflateEnd(&z);
    }

    static bool compress(z_stream &z, Slot &s) {
        if (deflateReset(&z) != Z_OK) return false;
        if (!s.dict.empty() &&
            deflateSetDictionary(&z, s.dict.data(), static_cast<uInt>(s.dict.size())) != Z_OK)
            return false;
        s.out.resize(deflateBound(&z, static_cast<uLong>(s.in.size())) + 64);
        z.next_in = s.in.data();
        z.avail_in = static_cast<uInt>(s.in.size());
        std::size_t len = 0;
        for (;;) {
            z.next_out = s.out.data() + len;
            z.avail_out = static_cast<uInt>(s.out.size() - len);
            int rc = deflate(&z, s.last ? Z_FINISH : Z_SYNC_FLUSH);
            len = s.out.size() - z.avail_out;
            if (rc == Z_STREAM_ERROR) return false;
            if (s.last ? rc == Z_STREAM_END : (z.avail_in == 0 && z.avail_out > 0)) break;
            if (z.avail_out == 0) s.out.resize(s.out.size() * 2);  // bound too small: grow
        }
        s.out.resize(len);
        s.crc = crc32(0L, s.in.data(), static_cast<uInt>(s.in.size()));
        return true;
    }

    void stop() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stopping_ = true;
        }
        work_.notify_all();
        for (std::thread &t : workers_) t.join();
        workers_.clear();
    }

    int fd_, level_;
    std::vector<Slot> slots_;
    std::vector<unsigned char> cur_, tail_;
    unsigned long long submitted_ = 0, taken_ = 0, written_ = 0, total_ = 0;
    unsigned long crc_ = crc32(0L, Z_NULL, 0);
    bool stopping_ = false;
    std::mutex mutex_;
    std::condition_variable work_, done_;
    std::vector<std::thread> workers_;
};

}  // namespace hashfasta

#endif
