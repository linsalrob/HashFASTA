// HashFASTA v0.1 converter (C++ port of the Python reference).
//
// Usage: hashfasta INPUT [-o OUTPUT]   ('-' means stdin / stdout)
//
// zlib's gzread() detects gzip by its magic bytes, reads plain input
// transparently and reads concatenated gzip members as one stream.

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
constexpr unsigned kIoBuffer = 1u << 17;
const char *const kOutputSuffix = ".hash.fasta.gz";
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

// RAII wrapper around a zlib gzFile.
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

bool is_space(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f' || c == '\r';
}

bool ends_with(const std::string &s, const std::string &suffix) {
    return s.size() >= suffix.size() && s.compare(s.size() - suffix.size(), suffix.size(), suffix) == 0;
}

std::string default_output_path(std::string name) {
    if (ends_with(name, ".gz")) name.resize(name.size() - 3);
    for (const char *suffix : kFastaSuffixes) {
        if (ends_with(name, suffix)) {
            name.resize(name.size() - std::strlen(suffix));
            break;
        }
    }
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
void convert(GzFile &in, GzFile &out) {
    enum class State { LineStart, InHeader, InSeq } st = State::LineStart;
    std::vector<unsigned char> chunk(kIoBuffer);
    std::optional<std::string> header;
    std::string seq;
    std::size_t lineno = 0, header_line = 0;
    int n;

    while ((n = gzread(in.get(), chunk.data(), kIoBuffer)) > 0) {
        for (int i = 0; i < n; i++) {
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
                if (c == '\n') {
                    if (!header->empty() && header->back() == '\r') header->pop_back();
                    st = State::LineStart;
                } else {
                    header->push_back(static_cast<char>(c));
                }
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
    if (n < 0) throw Error("read failed: " + zlib_message(in.get()));
    if (!header) throw Error("input contains no FASTA records");
    if (st == State::InHeader && !header->empty() && header->back() == '\r') header->pop_back();
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

    GzFile in(input == "-" ? gzdopen(STDIN_FILENO, "rb") : gzopen(input.c_str(), "rb"));
    if (!in.get()) throw Error("cannot open input " + input + ": " + std::strerror(errno));
    gzbuffer(in.get(), kIoBuffer);

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
