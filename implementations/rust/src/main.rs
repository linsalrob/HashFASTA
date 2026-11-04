//! HashFASTA v0.1 converter (Rust port of the Python reference).
//!
//! Usage: hashfasta INPUT [-o OUTPUT]   ('-' means stdin / stdout)

use bzip2::bufread::MultiBzDecoder;
use flate2::bufread::MultiGzDecoder;
use flate2::write::GzEncoder;
use flate2::{Compression, GzBuilder};
use liblzma::bufread::XzDecoder;
use sha2::{Digest, Sha256};
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, BufWriter, Cursor, Read, Write};
use std::os::unix::fs::MetadataExt;
use std::process::ExitCode;

const PROG: &str = "hashfasta";
const LINE_WIDTH: usize = 80;
const COMPRESSION_SUFFIXES: [&str; 4] = [".gz", ".bgz", ".bz2", ".xz"];
const FASTA_SUFFIXES: [&str; 8] = [
    ".fa", ".fas", ".fasta", ".fna", ".ffn", ".faa", ".frn", ".fsa",
];
const OUTPUT_SUFFIX: &str = ".hash.fasta.gz";
const USAGE: &str = "usage: hashfasta INPUT [-o OUTPUT]   ('-' means stdin/stdout)\n";

type Result<T> = std::result::Result<T, String>;

fn is_space(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | 0x0b | 0x0c | b'\r')
}

fn escape_header(header: &[u8], out: &mut Vec<u8>) {
    for &b in header {
        if b == b'\\' || b == b'[' || b == b']' {
            out.push(b'\\');
        }
        out.push(b);
    }
}

fn default_output_path<'a>(input: &'a str) -> String {
    let strip = |name: &'a str, suffixes: &[&str]| -> &'a str {
        suffixes
            .iter()
            .find_map(|s| name.strip_suffix(s))
            .unwrap_or(name)
    };
    let name = strip(strip(input, &COMPRESSION_SUFFIXES), &FASTA_SUFFIXES);
    format!("{name}{OUTPUT_SUFFIX}")
}

/// Wrap a reader, decompressing gzip, bzip2 or xz detected by magic bytes.
fn open_input(mut raw: Box<dyn Read>) -> Result<Box<dyn BufRead>> {
    let mut head = Vec::with_capacity(6);
    (&mut raw)
        .take(6)
        .read_to_end(&mut head)
        .map_err(|e| format!("I/O error: {e}"))?;
    for (magic, name) in [(&b"\x28\xb5\x2f\xfd"[..], "zstd"), (b"PK\x03\x04", "ZIP")] {
        if head.starts_with(magic) {
            return Err(format!(
                "{name}-compressed input is not supported; decompress it first"
            ));
        }
    }
    let rejoined = BufReader::with_capacity(1 << 16, Cursor::new(head.clone()).chain(raw));
    let buffered =
        |r: Box<dyn Read>| -> Box<dyn BufRead> { Box::new(BufReader::with_capacity(1 << 16, r)) };
    // Each decoder reads concatenated members/streams as one input.
    Ok(if head.starts_with(b"\x1f\x8b") {
        buffered(Box::new(MultiGzDecoder::new(rejoined)))
    } else if head.starts_with(b"BZh") {
        buffered(Box::new(MultiBzDecoder::new(rejoined)))
    } else if head == b"\xfd7zXZ\x00" {
        buffered(Box::new(XzDecoder::new_multi_decoder(rejoined)))
    } else {
        Box::new(rejoined)
    })
}

fn write_record<W: Write>(out: &mut W, header: &[u8], seq: &[u8]) -> io::Result<()> {
    let digest = Sha256::digest(seq);
    let mut line = Vec::with_capacity(header.len() + 80);
    line.extend_from_slice(b">sha256_");
    for b in digest {
        line.extend_from_slice(format!("{b:02x}").as_bytes());
    }
    line.extend_from_slice(b" [");
    escape_header(header, &mut line);
    line.extend_from_slice(b"]\n");
    out.write_all(&line)?;
    for chunk in seq.chunks(LINE_WIDTH) {
        out.write_all(chunk)?;
        out.write_all(b"\n")?;
    }
    Ok(())
}

/// Stream FASTA records from `inp` to `out`, holding one record in memory.
fn convert<R: BufRead + ?Sized, W: Write>(inp: &mut R, out: &mut W) -> Result<usize> {
    let mut header: Option<Vec<u8>> = None;
    let mut header_line = 0usize;
    let mut seq: Vec<u8> = Vec::new();
    let mut records = 0usize;
    let mut lineno = 0usize;
    let mut line = Vec::new();

    let finish = |header: &[u8], seq: &[u8], header_line: usize, out: &mut W| -> Result<()> {
        if seq.is_empty() {
            return Err(format!(
                "record at line {header_line} has an empty sequence"
            ));
        }
        write_record(out, header, seq).map_err(|e| format!("I/O error: {e}"))
    };

    loop {
        line.clear();
        let n = inp
            .read_until(b'\n', &mut line)
            .map_err(|e| format!("I/O error: {e}"))?;
        if n == 0 {
            break;
        }
        lineno += 1;
        if line[0] == b'>' {
            if let Some(h) = &header {
                finish(h, &seq, header_line, out)?;
                records += 1;
            }
            // Every CR is removed from the definition line (spec section 2).
            header = Some(
                line[1..]
                    .iter()
                    .copied()
                    .filter(|&b| b != b'\r' && b != b'\n')
                    .collect(),
            );
            header_line = lineno;
            seq.clear();
            continue;
        }
        // A line starting with ';' is a comment (spec section 3).
        if line[0] == b';' {
            continue;
        }
        for &b in &line {
            if is_space(b) {
                continue;
            }
            if header.is_none() {
                return Err(format!(
                    "line {lineno}: sequence data before the first '>' header"
                ));
            }
            if b == b';' {
                return Err(format!(
                    "line {lineno}: invalid sequence byte ';' (allowed only at the start \
                     of a comment line) in record starting at line {header_line}"
                ));
            }
            if !(0x21..0x7f).contains(&b) {
                return Err(format!(
                    "line {lineno}: invalid sequence byte 0x{b:02x} (non-ASCII or \
                     non-printable) in record starting at line {header_line}"
                ));
            }
            seq.push(b.to_ascii_uppercase());
        }
    }
    match &header {
        None => Err("input contains no FASTA records".to_string()),
        Some(h) => {
            finish(h, &seq, header_line, out)?;
            Ok(records + 1)
        }
    }
}

fn same_file(a: &str, b: &str) -> bool {
    match (fs::metadata(a), fs::metadata(b)) {
        (Ok(ma), Ok(mb)) => ma.dev() == mb.dev() && ma.ino() == mb.ino(),
        _ => false,
    }
}

/// Exclusively create a temporary sibling of `output`. `create_new` (O_EXCL)
/// refuses any existing name, including a symlink or hard link to the input,
/// so a planted temporary path can never redirect the write.
fn create_temp(output: &str) -> Result<(String, File)> {
    for n in 0..1000 {
        let path = format!("{output}.tmp.{}.{n}", std::process::id());
        match fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
        {
            Ok(fh) => return Ok((path, fh)),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(format!("cannot create {output}: {e}")),
        }
    }
    Err(format!("cannot create a temporary file beside {output}"))
}

fn gz_writer<W: Write>(w: W) -> GzEncoder<W> {
    GzBuilder::new().mtime(0).write(w, Compression::default())
}

fn run(input: &str, output: Option<&str>) -> Result<()> {
    let output = match output {
        Some(o) => o.to_string(),
        None if input == "-" => "-".to_string(),
        None => default_output_path(input),
    };
    if input != "-" && output != "-" && same_file(input, &output) {
        return Err(format!("output path {output} would overwrite the input"));
    }

    let raw: Box<dyn Read> = if input == "-" {
        Box::new(io::stdin())
    } else {
        Box::new(File::open(input).map_err(|e| format!("cannot open input {input}: {e}"))?)
    };
    let mut inp = open_input(raw)?;

    if output == "-" {
        let stdout = io::stdout();
        let mut gz = gz_writer(BufWriter::new(stdout.lock()));
        convert(&mut inp, &mut gz)?;
        gz.finish()
            .and_then(|mut w| w.flush())
            .map_err(|e| format!("I/O error: {e}"))?;
        return Ok(());
    }

    // Write a temporary sibling, renamed into place only on success.
    let (tmp, fh) = create_temp(&output)?;
    let result = (|| -> Result<()> {
        let mut gz = gz_writer(BufWriter::new(fh));
        convert(&mut inp, &mut gz)?;
        let w = gz.finish().map_err(|e| format!("I/O error: {e}"))?;
        let fh = w.into_inner().map_err(|e| format!("I/O error: {e}"))?;
        fh.sync_all().map_err(|e| format!("I/O error: {e}"))?;
        fs::rename(&tmp, &output).map_err(|e| format!("cannot write {output}: {e}"))
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}

fn parse_args(args: &[String]) -> Option<(String, Option<String>)> {
    let mut input = None;
    let mut output = None;
    let mut i = 0;
    while i < args.len() {
        let arg = args[i].as_str();
        if arg == "-h" || arg == "--help" {
            print!("{USAGE}");
            std::process::exit(0);
        }
        if arg == "-o" {
            output = Some(args.get(i + 1)?.clone());
            i += 2;
            continue;
        }
        if (arg != "-" && arg.starts_with('-')) || input.is_some() {
            return None;
        }
        input = Some(arg.to_string());
        i += 1;
    }
    Some((input?, output))
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some((input, output)) = parse_args(&args) else {
        eprint!("{USAGE}");
        return ExitCode::from(2);
    };
    match run(&input, output.as_deref()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(msg) => {
            eprintln!("{PROG}: error: {msg}");
            ExitCode::from(1)
        }
    }
}
