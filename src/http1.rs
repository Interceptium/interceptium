//! Minimal, strict HTTP/1.1 message handling on tokio streams.

use std::io::{self, Read};
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWrite, AsyncWriteExt};

pub const MAX_HEAD: usize = 64 * 1024;
pub const MAX_BUFFERED_BODY: usize = 64 * 1024 * 1024;
const COPY_CHUNK: usize = 64 * 1024;

pub fn protocol_error(msg: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, msg.to_string())
}

/// Ordered, case-insensitive header list that keeps the original spelling.
#[derive(Clone, Debug, Default)]
pub struct Headers(pub Vec<(String, String)>);

impl Headers {
    pub fn get(&self, name: &str) -> &str {
        self.0.iter().find(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v.as_str()).unwrap_or("")
    }
    pub fn has(&self, name: &str) -> bool {
        self.0.iter().any(|(k, _)| k.eq_ignore_ascii_case(name))
    }
    pub fn remove(&mut self, name: &str) {
        self.0.retain(|(k, _)| !k.eq_ignore_ascii_case(name));
    }
    pub fn set(&mut self, name: &str, value: &str) {
        self.remove(name);
        self.0.push((name.to_string(), value.to_string()));
    }
}

#[derive(Clone, Debug)]
pub struct Message {
    pub start_line: String,
    pub headers: Headers,
}

impl Message {
    pub fn method(&self) -> String {
        self.start_line.split(' ').next().unwrap_or("").to_ascii_uppercase()
    }
    pub fn target(&self) -> &str {
        self.start_line.split(' ').nth(1).unwrap_or("/")
    }
    pub fn set_target(&mut self, value: &str) {
        let mut parts: Vec<&str> = self.start_line.split(' ').collect();
        if parts.len() > 1 {
            parts[1] = value;
        }
        self.start_line = parts.join(" ");
    }
    pub fn status(&self) -> u16 {
        self.start_line.split(' ').nth(1).and_then(|s| s.parse().ok()).unwrap_or(0)
    }
    pub fn head_bytes(&self) -> Vec<u8> {
        let mut out = latin1(&self.start_line);
        out.extend_from_slice(b"\r\n");
        for (k, v) in &self.headers.0 {
            out.extend(latin1(k));
            out.extend_from_slice(b": ");
            out.extend(latin1(v));
            out.extend_from_slice(b"\r\n");
        }
        out.extend_from_slice(b"\r\n");
        out
    }
    pub fn wants_close(&self) -> bool {
        let conn = self.headers.get("connection").to_ascii_lowercase();
        if conn.contains("close") {
            return true;
        }
        self.start_line.to_ascii_uppercase().ends_with("HTTP/1.0") && !conn.contains("keep-alive")
    }
    pub fn is_chunked(&self) -> bool {
        self.headers.get("transfer-encoding").to_ascii_lowercase().contains("chunked")
    }
}

fn latin1(s: &str) -> Vec<u8> {
    s.chars().map(|c| if (c as u32) < 256 { c as u8 } else { b'?' }).collect()
}

fn from_latin1(b: &[u8]) -> String {
    b.iter().map(|&c| c as char).collect()
}

async fn read_line<R: AsyncBufRead + Unpin>(r: &mut R, limit: usize) -> io::Result<Vec<u8>> {
    let mut line = Vec::new();
    loop {
        let buf = r.fill_buf().await?;
        if buf.is_empty() {
            return Ok(line);
        }
        if let Some(i) = buf.iter().position(|&b| b == b'\n') {
            line.extend_from_slice(&buf[..=i]);
            r.consume(i + 1);
            return Ok(line);
        }
        let n = buf.len();
        line.extend_from_slice(buf);
        r.consume(n);
        if line.len() > limit {
            return Err(protocol_error("line too long"));
        }
    }
}

/// Read a request or response head. `Ok(None)` on a clean end of stream.
pub async fn read_head<R: AsyncBufRead + Unpin>(r: &mut R) -> io::Result<Option<Message>> {
    let mut total = 0usize;
    let mut lines: Vec<String> = Vec::new();
    loop {
        let raw = read_line(r, MAX_HEAD).await?;
        if raw.is_empty() {
            if total == 0 {
                return Ok(None);
            }
            return Err(protocol_error("connection closed mid-header"));
        }
        total += raw.len();
        if total > MAX_HEAD {
            return Err(protocol_error("header too large"));
        }
        if !raw.ends_with(b"\r\n") {
            if raw.ends_with(b"\n") {
                return Err(protocol_error("bare LF in header"));
            }
            return Err(protocol_error("connection closed mid-header"));
        }
        let text = from_latin1(&raw[..raw.len() - 2]);
        if text.is_empty() {
            if lines.is_empty() {
                continue; // tolerate a stray CRLF between messages
            }
            break;
        }
        lines.push(text);
    }
    let start = lines.remove(0);
    let mut headers = Vec::with_capacity(lines.len());
    for line in lines {
        let Some((k, v)) = line.split_once(':') else {
            return Err(protocol_error("bad header line"));
        };
        headers.push((k.trim().to_string(), v.trim().to_string()));
    }
    Ok(Some(Message { start_line: start, headers: Headers(headers) }))
}

fn has_body(msg: &Message, request_method: Option<&str>) -> bool {
    match request_method {
        None => msg.headers.has("content-length") || msg.is_chunked(),
        Some(m) => {
            let s = msg.status();
            !(m == "HEAD" || s == 204 || s == 304 || (100..200).contains(&s))
        }
    }
}

fn content_length(msg: &Message) -> io::Result<Option<usize>> {
    let v = msg.headers.get("content-length");
    if v.is_empty() {
        return Ok(None);
    }
    if !v.bytes().all(|b| b.is_ascii_digit()) {
        return Err(protocol_error("bad content-length"));
    }
    v.parse().map(Some).map_err(|_| protocol_error("bad content-length"))
}

fn chunk_size(line: &[u8]) -> io::Result<usize> {
    let text = from_latin1(line);
    let size = text.split(';').next().unwrap_or("").trim();
    if size.is_empty() {
        return Ok(0);
    }
    usize::from_str_radix(size, 16).map_err(|_| protocol_error("bad chunk size"))
}

/// Read a whole body into memory, undoing the transfer framing.
pub async fn read_body<R: AsyncBufRead + Unpin>(r: &mut R, msg: &Message, request_method: Option<&str>) -> io::Result<Vec<u8>> {
    if !has_body(msg, request_method) {
        return Ok(Vec::new());
    }
    if msg.is_chunked() {
        let mut out = Vec::new();
        loop {
            let line = read_line(r, 4096).await?;
            if line.is_empty() {
                return Err(io::ErrorKind::UnexpectedEof.into());
            }
            let size = chunk_size(&line)?;
            if size == 0 {
                loop {
                    let t = read_line(r, MAX_HEAD).await?;
                    if t.is_empty() || t == b"\r\n" {
                        return Ok(out);
                    }
                }
            }
            if out.len() + size > MAX_BUFFERED_BODY {
                return Err(protocol_error("body too large"));
            }
            let start = out.len();
            out.resize(start + size, 0);
            r.read_exact(&mut out[start..]).await?;
            let mut crlf = [0u8; 2];
            r.read_exact(&mut crlf).await?;
        }
    }
    if let Some(len) = content_length(msg)? {
        if len > MAX_BUFFERED_BODY {
            return Err(protocol_error("body too large"));
        }
        let mut out = vec![0u8; len];
        r.read_exact(&mut out).await?;
        return Ok(out);
    }
    if request_method.is_none() {
        return Ok(Vec::new());
    }
    let mut out = Vec::new();
    let mut chunk = vec![0u8; COPY_CHUNK];
    loop {
        let n = r.read(&mut chunk).await?;
        if n == 0 {
            return Ok(out);
        }
        out.extend_from_slice(&chunk[..n]);
        if out.len() > MAX_BUFFERED_BODY {
            return Err(protocol_error("body too large"));
        }
    }
}

async fn copy_exact<R: AsyncBufRead + Unpin, W: AsyncWrite + Unpin>(r: &mut R, w: &mut W, mut remaining: usize) -> io::Result<()> {
    let mut buf = vec![0u8; COPY_CHUNK.min(remaining.max(1))];
    while remaining > 0 {
        let want = remaining.min(buf.len());
        r.read_exact(&mut buf[..want]).await?;
        w.write_all(&buf[..want]).await?;
        remaining -= want;
    }
    Ok(())
}

/// Copy a body unchanged. Returns false if it ran until end of stream (connection not reusable).
pub async fn relay_body<R: AsyncBufRead + Unpin, W: AsyncWrite + Unpin>(
    r: &mut R,
    w: &mut W,
    msg: &Message,
    request_method: Option<&str>,
) -> io::Result<bool> {
    if !has_body(msg, request_method) {
        return Ok(true);
    }
    if msg.is_chunked() {
        loop {
            let line = read_line(r, 4096).await?;
            if line.is_empty() {
                return Err(io::ErrorKind::UnexpectedEof.into());
            }
            w.write_all(&line).await?;
            let size = chunk_size(&line)?;
            if size == 0 {
                loop {
                    let t = read_line(r, MAX_HEAD).await?;
                    if t.is_empty() {
                        return Err(io::ErrorKind::UnexpectedEof.into());
                    }
                    w.write_all(&t).await?;
                    if t == b"\r\n" {
                        w.flush().await?;
                        return Ok(true);
                    }
                }
            }
            copy_exact(r, w, size + 2).await?;
        }
    }
    if let Some(len) = content_length(msg)? {
        copy_exact(r, w, len).await?;
        w.flush().await?;
        return Ok(true);
    }
    if request_method.is_none() {
        return Ok(true);
    }
    tokio::io::copy_buf(r, w).await?;
    w.flush().await?;
    Ok(false)
}

/// Describe an uncompressed, fixed-length body.
pub fn set_plain_body(msg: &mut Message, len: usize) {
    msg.headers.remove("transfer-encoding");
    msg.headers.remove("content-encoding");
    msg.headers.set("Content-Length", &len.to_string());
}

/// Undo Content-Encoding. None if it can't be read (the caller passes the body through).
pub fn decode_content(body: &[u8], encoding: &str) -> Option<Vec<u8>> {
    let enc = encoding.trim().to_ascii_lowercase();
    let mut out = Vec::new();
    let limited = |r: &mut dyn Read, out: &mut Vec<u8>| -> Option<()> {
        r.take(MAX_BUFFERED_BODY as u64 + 1).read_to_end(out).ok()?;
        (out.len() <= MAX_BUFFERED_BODY).then_some(())
    };
    match enc.as_str() {
        "" | "identity" => return Some(body.to_vec()),
        "gzip" | "x-gzip" => limited(&mut flate2::read::MultiGzDecoder::new(body), &mut out)?,
        "deflate" => {
            if limited(&mut flate2::read::ZlibDecoder::new(body), &mut out).is_none() {
                out.clear();
                limited(&mut flate2::read::DeflateDecoder::new(body), &mut out)?;
            }
        }
        "zstd" => {
            let mut src = body;
            let mut dec = ruzstd::decoding::StreamingDecoder::new(&mut src).ok()?;
            limited(&mut dec, &mut out)?;
        }
        _ => return None,
    }
    Some(out)
}

pub fn simple_response(status: u16, reason: &str, body: &[u8], content_type: &str) -> Vec<u8> {
    let mut out = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nCache-Control: no-store\r\n\r\n",
        body.len()
    )
    .into_bytes();
    out.extend_from_slice(body);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tokio::io::BufReader;

    fn rt() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap()
    }

    #[test]
    fn heads_and_bodies() {
        rt().block_on(async {
            let raw: &[u8] = b"POST /x HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2;x=1\r\nde\r\n0\r\nT: 1\r\n\r\nGET / HTTP/1.1\r\nContent-Length: 2\r\n\r\nhi";
            let mut r = BufReader::new(raw);
            let m = read_head(&mut r).await.unwrap().unwrap();
            assert_eq!(m.method(), "POST");
            assert_eq!(m.target(), "/x");
            assert_eq!(read_body(&mut r, &m, None).await.unwrap(), b"abcde");
            let m2 = read_head(&mut r).await.unwrap().unwrap();
            assert_eq!(read_body(&mut r, &m2, None).await.unwrap(), b"hi");
            assert!(read_head(&mut r).await.unwrap().is_none());
        });
    }

    #[test]
    fn relay_keeps_framing() {
        rt().block_on(async {
            let raw: &[u8] = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n";
            let mut r = BufReader::new(raw);
            let m = read_head(&mut r).await.unwrap().unwrap();
            let mut out = Vec::new();
            assert!(relay_body(&mut r, &mut out, &m, Some("GET")).await.unwrap());
            assert_eq!(out, b"3\r\nabc\r\n0\r\n\r\n");
        });
    }

    #[test]
    fn rejects_bad_input() {
        rt().block_on(async {
            let mut r = BufReader::new(&b"GET / HTTP/1.1\r\nnocolon\r\n\r\n"[..]);
            assert!(read_head(&mut r).await.is_err());
            let mut r = BufReader::new(&b"GET / HTTP/1.1\r\nContent-Length: -1\r\n\r\n"[..]);
            let m = read_head(&mut r).await.unwrap().unwrap();
            assert!(read_body(&mut r, &m, None).await.is_err());
        });
    }

    #[test]
    fn decodes() {
        let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        gz.write_all(b"hello").unwrap();
        assert_eq!(decode_content(&gz.finish().unwrap(), "gzip").unwrap(), b"hello");
        assert_eq!(decode_content(b"x", "br"), None);
        assert_eq!(decode_content(b"x", "identity").unwrap(), b"x");
    }
}
