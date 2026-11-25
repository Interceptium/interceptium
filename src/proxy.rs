//! The proxy server.
//!
//! * Listens on 127.0.0.1 only; nothing else on the network can reach it.
//! * Speaks the standard HTTP proxy protocol (CONNECT); Roblox is pointed at it with the
//!   HTTPS_PROXY environment variable, so no hosts-file or system changes are needed.
//! * Only the Roblox asset hosts are decrypted. Every other CONNECT is a blind byte tunnel.
//! * Upstream connections use the system's trusted CAs with full certificate and hostname checks.

use crate::certs::{provider, CertAuthority};
use crate::config::ConfigStore;
use crate::http1::{self, protocol_error, Message};
use crate::router::{is_intercepted, AssetRouter, Single, ASSET_HOST, BATCH_PATH, CDN_HOSTS};
use std::io;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncWrite, AsyncWriteExt, BufReader, ReadBuf};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::watch;
use tokio::task::JoinSet;
use tokio::time::timeout;

pub const LISTEN_HOST: &str = "127.0.0.1";
const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);
const IDLE_TIMEOUT: Duration = Duration::from_secs(120);

pub type Resolver = Arc<dyn Fn(&str, u16) -> (String, u16) + Send + Sync>;

pub fn default_upstream() -> Arc<rustls::ClientConfig> {
    let mut roots = rustls::RootCertStore::empty();
    let found = rustls_native_certs::load_native_certs();
    for cert in found.certs {
        let _ = roots.add(cert);
    }
    let mut config = rustls::ClientConfig::builder_with_provider(provider())
        .with_safe_default_protocol_versions()
        .expect("TLS versions")
        .with_root_certificates(roots)
        .with_no_client_auth();
    config.alpn_protocols = vec![b"http/1.1".to_vec()];
    Arc::new(config)
}

#[derive(Clone)]
pub struct Shared {
    pub config: Arc<ConfigStore>,
    pub ca: Arc<CertAuthority>,
    pub router: Arc<AssetRouter>,
    pub upstream: Arc<rustls::ClientConfig>,
    pub resolve: Option<Resolver>,
}

impl Shared {
    fn target(&self, host: &str, port: u16) -> (String, u16) {
        match &self.resolve {
            Some(f) => f(host, port),
            None => (host.to_string(), port),
        }
    }
}

/// A running listener. Dropping it (or calling `stop`) closes every connection.
pub struct ProxyHandle {
    pub port: u16,
    stop: watch::Sender<bool>,
}

impl ProxyHandle {
    pub fn stop(&self) {
        let _ = self.stop.send(true);
    }
}

impl Drop for ProxyHandle {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Bind to `port` (trying the next nine if busy; 0 = any free port) and serve on `rt`.
pub fn start(rt: &tokio::runtime::Handle, shared: Shared, port: u16) -> io::Result<ProxyHandle> {
    let candidates: Vec<u16> = if port == 0 { vec![0] } else { (0..10).filter_map(|i| port.checked_add(i)).collect() };
    let mut last = None;
    let mut listener = None;
    for p in candidates {
        let bound = rt.block_on(async { TcpListener::bind((LISTEN_HOST, p)).await });
        match bound {
            Ok(l) => {
                listener = Some(l);
                break;
            }
            Err(e) => last = Some(e),
        }
    }
    let listener = listener.ok_or_else(|| {
        io::Error::new(io::ErrorKind::AddrInUse, format!("could not listen on {LISTEN_HOST}:{port}: {}", last.map(|e| e.to_string()).unwrap_or_default()))
    })?;
    let port = listener.local_addr()?.port();
    let (tx, mut rx) = watch::channel(false);
    crate::log!("Proxy", "listening on http://{LISTEN_HOST}:{port}");
    rt.spawn(async move {
        let mut tasks = JoinSet::new();
        loop {
            tokio::select! {
                _ = rx.changed() => break,
                accepted = listener.accept() => {
                    if let Ok((stream, _)) = accepted {
                        let _ = stream.set_nodelay(true);
                        let s = shared.clone();
                        tasks.spawn(async move { handle(s, stream).await });
                    }
                }
                Some(_) = tasks.join_next(), if !tasks.is_empty() => {}
            }
        }
        tasks.abort_all();
        crate::log!("Proxy", "stopped");
    });
    Ok(ProxyHandle { port, stop: tx })
}

pub fn split_host_port(authority: &str, default_port: u16) -> io::Result<(String, u16)> {
    let a = authority.trim();
    let (host, port) = if let Some(rest) = a.strip_prefix('[') {
        let (h, r) = rest.split_once(']').ok_or_else(|| protocol_error("bad host"))?;
        (h, r.trim_start_matches(':'))
    } else if a.matches(':').count() == 1 {
        a.split_once(':').unwrap()
    } else {
        (a, "")
    };
    if host.is_empty() {
        return Err(protocol_error("missing host"));
    }
    let port = if port.is_empty() {
        default_port
    } else {
        port.parse().map_err(|_| protocol_error("bad port"))?
    };
    Ok((host.to_ascii_lowercase(), port))
}

async fn handle(s: Shared, stream: TcpStream) {
    let mut client = BufReader::with_capacity(1 << 16, stream);
    let result: io::Result<()> = async {
        let Some(head) = timeout(IDLE_TIMEOUT, http1::read_head(&mut client)).await.map_err(|_| io::Error::from(io::ErrorKind::TimedOut))?? else {
            return Ok(());
        };
        if head.method() == "CONNECT" {
            handle_connect(&s, head, client).await
        } else if head.target().to_ascii_lowercase().starts_with("http://") {
            handle_plain_http(&s, head, client).await
        } else {
            // Not a proxy request (for example a web page probing localhost): refuse.
            client.write_all(&http1::simple_response(400, "Bad Request", b"Interceptium is a proxy.\n", "text/plain")).await?;
            client.shutdown().await
        }
    }
    .await;
    if let Err(e) = result {
        if e.kind() == io::ErrorKind::Other {
            crate::log!("Error", "{e}");
        }
    }
}

async fn connect_tcp(host: &str, port: u16) -> io::Result<TcpStream> {
    let stream = timeout(CONNECT_TIMEOUT, TcpStream::connect((host, port)))
        .await
        .map_err(|_| io::Error::from(io::ErrorKind::TimedOut))??;
    let _ = stream.set_nodelay(true);
    Ok(stream)
}

async fn handle_connect(s: &Shared, head: Message, mut client: BufReader<TcpStream>) -> io::Result<()> {
    let (host, port) = split_host_port(head.target(), 443)?;
    if is_intercepted(&host) && port == 443 {
        let config = s.ca.server_config(&host).map_err(io::Error::other)?;
        client.write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n").await?;
        let prefix = client.buffer().to_vec();
        let stream = Prefixed { prefix, pos: 0, inner: client.into_inner() };
        let tls = timeout(IDLE_TIMEOUT, tokio_rustls::TlsAcceptor::from(config).accept(stream))
            .await
            .map_err(|_| io::Error::from(io::ErrorKind::TimedOut))??;
        return mitm_loop(s, &host, tls).await;
    }
    // Blind tunnel: bytes pass through untouched and unread.
    let (up_host, up_port) = s.target(&host, port);
    let mut upstream = match connect_tcp(&up_host, up_port).await {
        Ok(u) => u,
        Err(_) => {
            client.write_all(&http1::simple_response(502, "Bad Gateway", b"upstream unreachable\n", "text/plain")).await?;
            return Ok(());
        }
    };
    client.write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n").await?;
    let _ = tokio::io::copy_bidirectional(&mut client, &mut upstream).await;
    Ok(())
}

async fn handle_plain_http(s: &Shared, mut head: Message, mut client: BufReader<TcpStream>) -> io::Result<()> {
    let rest = head.target()[7..].to_string();
    let (authority, path) = match rest.find('/') {
        Some(i) => (rest[..i].to_string(), rest[i..].to_string()),
        None => (rest.clone(), "/".to_string()),
    };
    let (host, port) = split_host_port(&authority, 80)?;
    head.set_target(&path);
    head.headers.set("Connection", "close");
    head.headers.remove("proxy-connection");
    let (up_host, up_port) = s.target(&host, port);
    let mut upstream = match connect_tcp(&up_host, up_port).await {
        Ok(u) => BufReader::new(u),
        Err(_) => {
            client.write_all(&http1::simple_response(502, "Bad Gateway", b"upstream unreachable\n", "text/plain")).await?;
            return Ok(());
        }
    };
    upstream.write_all(&head.head_bytes()).await?;
    http1::relay_body(&mut client, &mut upstream, &head, None).await?;
    upstream.flush().await?;
    let _ = tokio::io::copy_buf(&mut upstream, &mut client).await;
    client.shutdown().await
}

type TlsUp = tokio_rustls::client::TlsStream<TcpStream>;

struct Up {
    r: BufReader<tokio::io::ReadHalf<TlsUp>>,
    w: tokio::io::WriteHalf<TlsUp>,
}

async fn open_upstream(s: &Shared, host: &str) -> io::Result<Up> {
    let (h, p) = s.target(host, 443);
    let tcp = connect_tcp(&h, p).await?;
    let name = rustls::pki_types::ServerName::try_from(host.to_string()).map_err(|_| protocol_error("bad host"))?;
    let tls = timeout(CONNECT_TIMEOUT, tokio_rustls::TlsConnector::from(s.upstream.clone()).connect(name, tcp))
        .await
        .map_err(|_| io::Error::from(io::ErrorKind::TimedOut))??;
    let (r, w) = tokio::io::split(tls);
    Ok(Up { r: BufReader::with_capacity(1 << 16, r), w })
}

async fn send_local<W: AsyncWrite + Unpin>(w: &mut W, path: &str) -> io::Result<()> {
    let data = tokio::fs::metadata(path).await.ok().filter(|m| m.is_file()).map(|_| ());
    let bytes = match data {
        Some(()) => tokio::fs::read(path).await.ok(),
        None => None,
    };
    match bytes {
        Some(b) => w.write_all(&http1::simple_response(200, "OK", &b, "application/octet-stream")).await?,
        None => {
            crate::log!("File", "missing local file {path}");
            w.write_all(&http1::simple_response(404, "Not Found", b"replacement file missing\n", "text/plain")).await?
        }
    }
    w.flush().await
}

async fn mitm_loop<S: AsyncRead + AsyncWrite + Unpin>(s: &Shared, host: &str, client: S) -> io::Result<()> {
    let (cr, mut cw) = tokio::io::split(client);
    let mut cr = BufReader::with_capacity(1 << 16, cr);
    let mut up: Option<Up> = None;
    loop {
        let Some(mut req) = timeout(IDLE_TIMEOUT, http1::read_head(&mut cr)).await.map_err(|_| io::Error::from(io::ErrorKind::TimedOut))?? else {
            return Ok(());
        };
        if req.headers.get("expect").to_ascii_lowercase().contains("100-continue") {
            req.headers.remove("expect");
            cw.write_all(b"HTTP/1.1 100 Continue\r\n\r\n").await?;
            cw.flush().await?;
        }
        let mut body = http1::read_body(&mut cr, &req, None).await?;
        let rules = s.config.active_rules();
        let path = req.target().to_string();

        if CDN_HOSTS.contains(&host) {
            if let Some(local) = s.router.cdn_file_for(host, &path) {
                send_local(&mut cw, &local).await?;
                if req.wants_close() {
                    return Ok(());
                }
                continue;
            }
        }

        let mut is_batch = false;
        if host == ASSET_HOST {
            match AssetRouter::rewrite_single(&path, &rules) {
                Single::Remove => {
                    cw.write_all(&http1::simple_response(404, "Not Found", b"removed by Interceptium\n", "text/plain")).await?;
                    cw.flush().await?;
                    continue;
                }
                Single::File(local) => {
                    send_local(&mut cw, &local).await?;
                    continue;
                }
                Single::Replace(new_target) => req.set_target(&new_target),
                Single::Pass => {}
            }
            is_batch = req.method() == "POST" && path.split('?').next().unwrap_or("").trim_end_matches('/') == BATCH_PATH;
            if is_batch && !rules.is_empty() {
                if let Some(plain) = http1::decode_content(&body, req.headers.get("content-encoding")) {
                    if let Some(new_body) = s.router.rewrite_batch_request(&plain, &rules) {
                        body = new_body;
                        http1::set_plain_body(&mut req, body.len());
                    }
                }
                req.headers.set("Accept-Encoding", "gzip"); // an encoding we can always read back
            }
        }
        if req.is_chunked() || (!req.headers.has("content-length") && !body.is_empty()) {
            req.headers.remove("transfer-encoding");
            req.headers.set("Content-Length", &body.len().to_string());
        }

        let mut resp: Option<Message> = None;
        for attempt in 1..=2 {
            let fresh = up.is_none();
            if up.is_none() {
                match open_upstream(s, host).await {
                    Ok(u) => up = Some(u),
                    Err(e) => {
                        crate::log!("Upstream", "{host}: {e}");
                        cw.write_all(&http1::simple_response(502, "Bad Gateway", b"upstream failed\n", "text/plain")).await?;
                        cw.flush().await?;
                        return Ok(());
                    }
                }
            }
            let u = up.as_mut().unwrap();
            let mut out = req.head_bytes();
            out.extend_from_slice(&body);
            let got: io::Result<Option<Message>> = async {
                u.w.write_all(&out).await?;
                u.w.flush().await?;
                let mut r = timeout(IDLE_TIMEOUT, http1::read_head(&mut u.r)).await.map_err(|_| io::Error::from(io::ErrorKind::TimedOut))??;
                while let Some(m) = &r {
                    let st = m.status();
                    if (100..200).contains(&st) && st != 101 {
                        r = http1::read_head(&mut u.r).await?;
                    } else {
                        break;
                    }
                }
                Ok(r)
            }
            .await;
            resp = got.ok().flatten();
            if resp.is_some() || fresh || attempt == 2 {
                break;
            }
            up = None; // a reused connection went stale; retry once on a new one
        }
        let (Some(mut resp), Some(u)) = (resp, up.as_mut()) else {
            cw.write_all(&http1::simple_response(502, "Bad Gateway", b"no upstream response\n", "text/plain")).await?;
            cw.flush().await?;
            return Ok(());
        };
        let method = req.method();
        let mut reusable = true;
        if is_batch && s.router.has_pending() {
            let resp_body = http1::read_body(&mut u.r, &resp, Some(&method)).await?;
            if let Some(plain) = http1::decode_content(&resp_body, resp.headers.get("content-encoding")) {
                s.router.learn_batch_response(&plain);
            }
            if resp.is_chunked() || !resp.headers.has("content-length") {
                resp.headers.remove("transfer-encoding");
                resp.headers.set("Content-Length", &resp_body.len().to_string());
            }
            let mut out = resp.head_bytes();
            out.extend_from_slice(&resp_body);
            cw.write_all(&out).await?;
            cw.flush().await?;
        } else {
            cw.write_all(&resp.head_bytes()).await?;
            reusable = http1::relay_body(&mut u.r, &mut cw, &resp, Some(&method)).await?;
            cw.flush().await?;
        }
        if resp.wants_close() || !reusable {
            up = None;
            if !reusable {
                let _ = cw.shutdown().await;
                return Ok(());
            }
        }
        if req.wants_close() {
            let _ = cw.shutdown().await;
            return Ok(());
        }
    }
}

/// A stream that first replays bytes already read into a buffer.
struct Prefixed {
    prefix: Vec<u8>,
    pos: usize,
    inner: TcpStream,
}

impl AsyncRead for Prefixed {
    fn poll_read(mut self: Pin<&mut Self>, cx: &mut Context<'_>, buf: &mut ReadBuf<'_>) -> Poll<io::Result<()>> {
        if self.pos < self.prefix.len() {
            let n = (self.prefix.len() - self.pos).min(buf.remaining());
            let start = self.pos;
            buf.put_slice(&self.prefix[start..start + n]);
            self.pos += n;
            return Poll::Ready(Ok(()));
        }
        Pin::new(&mut self.inner).poll_read(cx, buf)
    }
}

impl AsyncWrite for Prefixed {
    fn poll_write(mut self: Pin<&mut Self>, cx: &mut Context<'_>, buf: &[u8]) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.inner).poll_write(cx, buf)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_shutdown(cx)
    }
}
