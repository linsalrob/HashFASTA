// HashFASTA v0.1 converter (C++ port of the Python reference).
//
// Usage: hashfasta INPUT [-o OUTPUT]   ('-' means stdin / stdout)
//
// Input format is detected from its magic bytes and decoded as a stream:
// gzip (zlib inflate), bzip2 (libbz2) or xz (liblzma), each reading
// concatenated members/streams as one input; anything else is plain text.
// Output is written with zlib's gzwrite().

#include <bzlib.h>
#include <fcntl.h>
#include <lzma.h>
#include <sys/stat.h>
#include <unistd.h>
#include <zlib.h>

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "sha256.hpp"

namespace hashfasta {
namespace {

constexpr std::size_t kLineWidth = 80;
constexpr std::size_t kIoBuffer = 1u << 17;
const char *const kOutputSuffix = ".hash.fasta.gz";
const char *const kCompressionSuffixes[] = {".gz", ".bgz", ".bz2", ".xz"};
const char *const kFastaSuffixes[] = {".fa", ".fas", ".fasta", ".fna", ".ffn", ".faa", ".frn", ".fsa"};
const char *const kUsage = "usage: hashfasta INPUT [-o OUTPUT]   ('-' means stdin/stdout)\n";

struct Error : std::runtime_error {
    using std::runtime_error::runtime_error;
};

std::string zlib_message(gzFile f) {
    int err = Z_OK;
    const char *msg = gzerror(f, &err);
    return err == Z_ERRNO ? std::strerror(errno) : msg;
}

// RAII wrapper around a zlib gzFile used for output.
class GzFile {
public:
    explicit GzFile(gzFile f) : f_(f) {}
    GzFile(const GzFile &) = delete;
    GzFile &operator=(const GzFile &) = delete;
    ~GzFile() {
        if (f_) gzclose(f_);
    }
    gzFile get() const { return f_; }
    void close() {
        int rc = gzclose(f_);
        f_ = nullptr;
        if (rc != Z_OK)
            throw Error(std::string("closing output failed (") +
                        (rc == Z_ERRNO ? std::strerror(errno) : zError(rc)) + ")");
    }
    void write(const void *data, std::size_t len) {
        if (len && gzwrite(f_, data, static_cast<unsigned>(len)) != static_cast<int>(len))
            throw Error("write failed: " + zlib_message(f_));
    }

private:
    gzFile f_;
};

// Streaming decoder over a file descriptor; format chosen from magic bytes.
class Reader {
public:
    explicit Reader(int fd) : fd_(fd), in_(kIoBuffer) {
        // Read at least 6 bytes (or to EOF) to see the magic number.
        while (len_ < 6) {
            ssize_t n = ::read(fd_, in_.data() + len_, in_.size() - len_);
            if (n < 0 && errno == EINTR) continue;
            if (n < 0) throw Error(std::string("read failed: ") + std::strerror(errno));
            if (n == 0) {
                eof_ = true;
                break;
            }
            len_ += static_cast<std::size_t>(n);
        }
        auto starts = [&](const char *magic, std::size_t n) {
            return len_ >= n && std::memcmp(in_.data(), magic, n) == 0;
        };
        if (starts("\x28\xb5\x2f\xfd", 4)) throw Error("zstd-compressed input is not supported; decompress it first");
        if (starts("PK\x03\x04", 4)) throw Error("ZIP-compressed input is not supported; decompress it first");
        if (starts("\x1f\x8b", 2)) {
            fmt_ = Format::Gzip;
            if (inflateInit2(&z_, 15 + 16) != Z_OK) throw Error("zlib initialisation failed");
        } else if (starts("BZh", 3)) {
            fmt_ = Format::Bzip2;
            if (BZ2_bzDecompressInit(&bz_, 0, 0) != BZ_OK) throw Error("bzip2 initialisation failed");
        } else if (starts("\xfd" "7zXZ\0", 6)) {
            fmt_ = Format::Xz;
            if (lzma_stream_decoder(&lz_, UINT64_MAX, LZMA_CONCATENATED) != LZMA_OK)
                throw Error("xz initialisation failed");
        }
    }
    Reader(const Reader &) = delete;
    Reader &operator=(const Reader &) = delete;
    ~Reader() {
        if (fmt_ == Format::Gzip) inflateEnd(&z_);
        if (fmt_ == Format::Bzip2) BZ2_bzDecompressEnd(&bz_);
        if (fmt_ == Format::Xz) lzma_end(&lz_);
        if (fd_ != STDIN_FILENO) ::close(fd_);
    }

    // Decoded bytes into out (up to cap); 0 at end of input. Throws on error.
    std::size_t read(unsigned char *out, std::size_t cap) {
        switch (fmt_) {
        case Format::Gzip: return read_gzip(out, cap);
        case Format::Bzip2: return read_bzip2(out, cap);
        case Format::Xz: return read_xz(out, cap);
        case Format::Plain: break;
        }
        if (pos_ == len_ && !eof_) fill();
        std::size_t n = std::min(cap, len_ - pos_);
        std::memcpy(out, in_.data() + pos_, n);
        pos_ += n;
        return n;
    }

private:
    enum class Format { Plain, Gzip, Bzip2, Xz };

    void fill() {
        ssize_t n;
        do n = ::read(fd_, in_.data(), in_.size());
        while (n < 0 && errno == EINTR);
        if (n < 0) throw Error(std::string("read failed: ") + std::strerror(errno));
        pos_ = 0;
        len_ = static_cast<std::size_t>(n);
        eof_ = n == 0;
    }

    bool exhausted() const { return eof_ && pos_ == len_; }

    std::size_t read_gzip(unsigned char *out, std::size_t cap) {
        for (;;) {
            if (pos_ == len_ && !eof_) fill();
            if (member_done_) {
                if (pos_ == len_) return 0;  // end of the last member
                inflateReset(&z_);
                member_done_ = false;
            }
            z_.next_in = in_.data() + pos_;
            z_.avail_in = static_cast<uInt>(len_ - pos_);
            z_.next_out = out;
            z_.avail_out = static_cast<uInt>(cap);
            int ret = inflate(&z_, Z_NO_FLUSH);
            pos_ = len_ - z_.avail_in;
            std::size_t produced = cap - z_.avail_out;
            if (ret == Z_STREAM_END) {
                member_done_ = true;
            } else if (ret == Z_BUF_ERROR) {
                if (exhausted()) throw Error("truncated gzip input");
            } else if (ret != Z_OK) {
                throw Error(std::string("corrupt gzip input: ") + (z_.msg ? z_.msg : zError(ret)));
            }
            if (produced) return produced;
        }
    }

    std::size_t read_bzip2(unsigned char *out, std::size_t cap) {
        for (;;) {
            if (pos_ == len_ && !eof_) fill();
            if (member_done_) {
                if (pos_ == len_) return 0;
                BZ2_bzDecompressEnd(&bz_);
                bz_ = bz_stream{};
                if (BZ2_bzDecompressInit(&bz_, 0, 0) != BZ_OK) throw Error("bzip2 initialisation failed");
                member_done_ = false;
            }
            bz_.next_in = reinterpret_cast<char *>(in_.data() + pos_);
            bz_.avail_in = static_cast<unsigned>(len_ - pos_);
            bz_.next_out = reinterpret_cast<char *>(out);
            bz_.avail_out = static_cast<unsigned>(cap);
            int ret = BZ2_bzDecompress(&bz_);
            pos_ = len_ - bz_.avail_in;
            std::size_t produced = cap - bz_.avail_out;
            if (ret == BZ_STREAM_END) {
                member_done_ = true;
            } else if (ret != BZ_OK) {
                throw Error("corrupt bzip2 input (error " + std::to_string(ret) + ")");
            } else if (!produced && exhausted()) {
                throw Error("truncated bzip2 input");
            }
            if (produced) return produced;
        }
    }

    std::size_t read_xz(unsigned char *out, std::size_t cap) {
        for (;;) {
            if (done_) return 0;
            if (pos_ == len_ && !eof_) fill();
            lz_.next_in = in_.data() + pos_;
            lz_.avail_in = len_ - pos_;
            lz_.next_out = out;
            lz_.avail_out = cap;
            lzma_ret ret = lzma_code(&lz_, eof_ ? LZMA_FINISH : LZMA_RUN);
            pos_ = len_ - lz_.avail_in;
            std::size_t produced = cap - lz_.avail_out;
            if (ret == LZMA_STREAM_END) {
                done_ = true;
            } else if (ret == LZMA_BUF_ERROR) {
                throw Error("truncated xz input");
            } else if (ret != LZMA_OK) {
                throw Error("corrupt xz input (liblzma error " + std::to_string(ret) + ")");
            }
            if (produced) return produced;
        }
    }

    int fd_;
    std::vector<unsigned char> in_;
    std::size_t pos_ = 0, len_ = 0;
    bool eof_ = false, member_done_ = false, done_ = false;
    Format fmt_ = Format::Plain;
    z_stream z_{};
    bz_stream bz_{};
    lzma_stream lz_ = LZMA_STREAM_INIT;
};

bool is_space(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f' || c == '\r';
}

bool ends_with(const std::string &s, const std::string &suffix) {
    return s.size() >= suffix.size() && s.compare(s.size() - suffix.size(), suffix.size(), suffix) == 0;
}

template <std::size_t N>
void strip_suffix(std::string &name, const char *const (&suffixes)[N]) {
    for (const char *suffix : suffixes) {
        if (ends_with(name, suffix)) {
            name.resize(name.size() - std::strlen(suffix));
            return;
        }
    }
}

std::string default_output_path(std::string name) {
    strip_suffix(name, kCompressionSuffixes);
    strip_suffix(name, kFastaSuffixes);
    return name + kOutputSuffix;
}

void write_record(GzFile &out, const std::string &header, const std::string &seq, std::size_t header_line) {
    if (seq.empty()) throw Error("record at line " + std::to_string(header_line) + " has an empty sequence");
    Sha256 sha;
    sha.update(reinterpret_cast<const unsigned char *>(seq.data()), seq.size());
    std::string line = ">sha256_" + sha.hexdigest() + " [";
    for (char c : header) {
        if (c == '\\' || c == '[' || c == ']') line.push_back('\\');
        line.push_back(c);
    }
    line += "]\n";
    out.write(line.data(), line.size());
    for (std::size_t i = 0; i < seq.size(); i += kLineWidth) {
        out.write(seq.data() + i, std::min(kLineWidth, seq.size() - i));
        out.write("\n", 1);
    }
}

std::string hex_byte(unsigned char c) {
    char buf[5];
    std::snprintf(buf, sizeof buf, "0x%02x", c);
    return buf;
}

// Stream FASTA records from in to out, holding one record in memory.
void convert(Reader &in, GzFile &out) {
    enum class State { LineStart, InHeader, InSeq } st = State::LineStart;
    std::vector<unsigned char> chunk(kIoBuffer);
    std::optional<std::string> header;
    std::string seq;
    std::size_t lineno = 0, header_line = 0, n;

    while ((n = in.read(chunk.data(), chunk.size())) > 0) {
        for (std::size_t i = 0; i < n; i++) {
            unsigned char c = chunk[i];
            if (st == State::LineStart) {
                lineno++;
                if (c == '>') {
                    if (header) write_record(out, *header, seq, header_line);
                    header.emplace();
                    seq.clear();
                    header_line = lineno;
                    st = State::InHeader;
                    continue;
                }
                st = State::InSeq;
            }
            if (st == State::InHeader) {
                // Every CR is removed from the definition line (spec section 2).
                if (c == '\n') st = State::LineStart;
                else if (c != '\r') header->push_back(static_cast<char>(c));
                continue;
            }
            if (c == '\n') {
                st = State::LineStart;
            } else if (is_space(c)) {
                continue;
            } else if (!header) {
                throw Error("line " + std::to_string(lineno) + ": sequence data before the first '>' header");
            } else if (c < 0x21 || c > 0x7e) {
                throw Error("line " + std::to_string(lineno) + ": invalid sequence byte " + hex_byte(c) +
                            " (non-ASCII or non-printable) in record starting at line " +
                            std::to_string(header_line));
            } else {
                seq.push_back(static_cast<char>(c >= 'a' && c <= 'z' ? c - 32 : c));
            }
        }
    }
    if (!header) throw Error("input contains no FASTA records");
    write_record(out, *header, seq, header_line);
}

bool same_file(const std::string &a, const std::string &b) {
    struct stat sa, sb;
    if (stat(a.c_str(), &sa) != 0 || stat(b.c_str(), &sb) != 0) return false;
    return sa.st_dev == sb.st_dev && sa.st_ino == sb.st_ino;
}

void run(const std::string &input, std::optional<std::string> output_opt) {
    std::string output = output_opt ? *output_opt : input == "-" ? "-" : default_output_path(input);
    if (input != "-" && output != "-" && same_file(input, output))
        throw Error("output path " + output + " would overwrite the input");

    int fd = input == "-" ? STDIN_FILENO : ::open(input.c_str(), O_RDONLY);
    if (fd < 0) throw Error("cannot open input " + input + ": " + std::strerror(errno));
    Reader in(fd);

    if (output == "-") {
        GzFile out(gzdopen(STDOUT_FILENO, "wb"));
        if (!out.get()) throw Error("cannot open stdout");
        gzbuffer(out.get(), kIoBuffer);
        convert(in, out);
        out.close();
        return;
    }

    // Write a temporary sibling, renamed into place only on success.
    const std::string tmp = output + ".tmp." + std::to_string(getpid());
    try {
        GzFile out(gzopen(tmp.c_str(), "wb"));
        if (!out.get()) throw Error("cannot create " + output + ": " + std::strerror(errno));
        gzbuffer(out.get(), kIoBuffer);
        convert(in, out);
        out.close();
        if (std::rename(tmp.c_str(), output.c_str()) != 0)
            throw Error("cannot write " + output + ": " + std::strerror(errno));
    } catch (...) {
        std::remove(tmp.c_str());
        throw;
    }
}

}  // namespace
}  // namespace hashfasta

int main(int argc, char **argv) {
    std::optional<std::string> input, output;
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg == "-h" || arg == "--help") {
            std::cout << hashfasta::kUsage;
            return 0;
        } else if (arg == "-o" && i + 1 < argc) {
            output = argv[++i];
        } else if ((arg.size() > 1 && arg[0] == '-') || input) {
            std::cerr << hashfasta::kUsage;
            return 2;
        } else {
            input = arg;
        }
    }
    if (!input) {
        std::cerr << hashfasta::kUsage;
        return 2;
    }
    try {
        hashfasta::run(*input, output);
    } catch (const std::exception &e) {
        std::cerr << "hashfasta: error: " << e.what() << '\n';
        return 1;
    }
    return 0;
}
