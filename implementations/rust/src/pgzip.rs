//! Parallel gzip writer: one gzip member compressed on several threads.
//!
//! Output is split into fixed-size blocks. Worker threads compress each block
//! as raw deflate, ended with a sync flush (the last block with a finish), so
//! the blocks join into a single deflate stream (the pigz technique). Blocks
//! are compressed independently: flate2's default backend (miniz_oxide) has
//! no preset dictionary, which costs a little compression at block starts.
//! The calling thread writes the blocks in order behind one gzip header and
//! combines their CRC-32s.

use flate2::{Compress, Compression, Crc, FlushCompress, Status};
use std::collections::VecDeque;
use std::io::{self, Write};
use std::sync::mpsc::{self, Receiver, Sender, SyncSender};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

const BLOCK: usize = 128 * 1024; // uncompressed bytes per job

struct Job {
    data: Vec<u8>,
    last: bool,
    reply: SyncSender<io::Result<Compressed>>,
}

struct Compressed {
    out: Vec<u8>,
    crc: Crc,
}

pub struct ParallelGz<W: Write> {
    inner: Option<W>,
    cur: Vec<u8>,
    jobs: Option<Sender<Job>>,
    pending: VecDeque<Receiver<io::Result<Compressed>>>,
    max_pending: usize,
    workers: Vec<JoinHandle<()>>,
    crc: Crc,
}

fn stopped() -> io::Error {
    io::Error::other("compression thread stopped")
}

impl<W: Write> ParallelGz<W> {
    pub fn new(mut inner: W, level: u32, threads: usize) -> io::Result<Self> {
        // gzip header: deflate, no flags, mtime 0, no extra flags, OS = Unix
        inner.write_all(&[0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 3])?;
        let (tx, rx) = mpsc::channel::<Job>();
        let rx = Arc::new(Mutex::new(rx));
        // Builder::spawn reports failure as an error (spawn would panic); on
        // failure, workers already started exit once `tx` is dropped.
        let workers = (0..threads)
            .map(|_| {
                let rx = Arc::clone(&rx);
                std::thread::Builder::new().spawn(move || worker(&rx, level))
            })
            .collect::<io::Result<Vec<_>>>()
            .map_err(|e| io::Error::other(format!("cannot start compression threads: {e}")))?;
        Ok(Self {
            inner: Some(inner),
            cur: Vec::with_capacity(BLOCK),
            jobs: Some(tx),
            pending: VecDeque::new(),
            max_pending: 2 * threads,
            workers,
            crc: Crc::new(),
        })
    }

    fn submit(&mut self, last: bool) -> io::Result<()> {
        while self.pending.len() >= self.max_pending {
            self.write_next()?;
        }
        let data = std::mem::replace(&mut self.cur, Vec::with_capacity(BLOCK));
        let (reply, result) = mpsc::sync_channel(1);
        let jobs = self.jobs.as_ref().ok_or_else(stopped)?;
        jobs.send(Job { data, last, reply })
            .map_err(|_| stopped())?;
        self.pending.push_back(result);
        Ok(())
    }

    /// Wait for the oldest unwritten block and write it out.
    fn write_next(&mut self) -> io::Result<()> {
        let result = self.pending.pop_front().ok_or_else(stopped)?;
        let block = result.recv().map_err(|_| stopped())??;
        self.inner
            .as_mut()
            .ok_or_else(stopped)?
            .write_all(&block.out)?;
        self.crc.combine(&block.crc);
        Ok(())
    }

    /// Compress what remains, write the gzip trailer and return the writer.
    pub fn finish(mut self) -> io::Result<W> {
        self.submit(true)?;
        while !self.pending.is_empty() {
            self.write_next()?;
        }
        let mut trailer = [0u8; 8];
        trailer[..4].copy_from_slice(&self.crc.sum().to_le_bytes());
        trailer[4..].copy_from_slice(&self.crc.amount().to_le_bytes()); // size mod 2^32
        let mut inner = self.inner.take().ok_or_else(stopped)?;
        inner.write_all(&trailer)?;
        Ok(inner)
    }
}

impl<W: Write> Write for ParallelGz<W> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let n = buf.len().min(BLOCK - self.cur.len());
        self.cur.extend_from_slice(&buf[..n]);
        if self.cur.len() == BLOCK {
            self.submit(false)?;
        }
        Ok(n)
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl<W: Write> Drop for ParallelGz<W> {
    fn drop(&mut self) {
        self.jobs = None; // workers exit once the queue is empty
        for worker in self.workers.drain(..) {
            let _ = worker.join();
        }
    }
}

fn worker(jobs: &Mutex<Receiver<Job>>, level: u32) {
    let mut z = Compress::new(Compression::new(level), false);
    loop {
        let job = match jobs.lock() {
            Ok(rx) => match rx.recv() {
                Ok(job) => job,
                Err(_) => return,
            },
            Err(_) => return,
        };
        z.reset();
        let mut crc = Crc::new();
        crc.update(&job.data);
        let result = compress(&mut z, &job.data, job.last).map(|out| Compressed { out, crc });
        let _ = job.reply.send(result);
    }
}

fn compress(z: &mut Compress, data: &[u8], last: bool) -> io::Result<Vec<u8>> {
    let flush = if last {
        FlushCompress::Finish
    } else {
        FlushCompress::Sync
    };
    let mut out = Vec::with_capacity(data.len() + data.len() / 8 + 64);
    let start = z.total_in();
    loop {
        let used = (z.total_in() - start) as usize;
        let status = z
            .compress_vec(&data[used..], &mut out, flush)
            .map_err(io::Error::other)?;
        let all_in = (z.total_in() - start) as usize == data.len();
        let complete = if last {
            status == Status::StreamEnd
        } else {
            all_in && out.len() < out.capacity()
        };
        if complete {
            return Ok(out);
        }
        out.reserve(out.capacity().max(4096));
    }
}
