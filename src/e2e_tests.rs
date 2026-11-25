//! End-to-end: a real client talks through the proxy to a fake Roblox server.
#![cfg(test)]

use crate::certs::{provider, CertAuthority};
use crate::config::ConfigStore;
use crate::http1;
use crate::proxy::{self, Shared};
use crate::router::AssetRouter;
use rcgen::{CertificateParams, IsCa, Issuer, KeyPair};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer, ServerName};
use serde_json::{json, Value};
use std::io::Write;
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};

fn temp(name: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("ie2e-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// A fake upstream with its own test CA: (port, roots that trust it).
async fn fake_roblox() -> (u16, rustls::RootCertStore) {
    let ca_key = KeyPair::generate().unwrap();
    let mut ca_params = CertificateParams::new(Vec::<String>::new()).unwrap();
    ca_params.is_ca = IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
    let ca_cert = ca_params.self_signed(&ca_key).unwrap();
    let issuer = Issuer::new(ca_params, ca_key);
    let key = KeyPair::generate().unwrap();
    let leaf = CertificateParams::new(vec!["assetdelivery.roblox.com".into(), "fts.rbxcdn.com".into()])
        .unwrap()
        .signed_by(&key, &issuer)
        .unwrap();
    let mut roots = rustls::RootCertStore::empty();
    roots.add(ca_cert.der().clone()).unwrap();
    let mut cfg = rustls::ServerConfig::builder_with_provider(provider())
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(vec![leaf.der().clone()], PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der())))
        .unwrap();
    cfg.alpn_protocols = vec![b"http/1.1".to_vec()];
    let acceptor = tokio_rustls::TlsAcceptor::from(Arc::new(cfg));
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        loop {
            let Ok((tcp, _)) = listener.accept().await else { return };
            let acceptor = acceptor.clone();
            tokio::spawn(async move {
                let Ok(tls) = acceptor.accept(tcp).await else { return };
                let (r, mut w) = tokio::io::split(tls);
                let mut r = BufReader::new(r);
                while let Ok(Some(req)) = http1::read_head(&mut r).await {
                    let body = http1::read_body(&mut r, &req, None).await.unwrap();
                    let path = req.target().to_string();
                    let reply: Vec<u8> = if path.starts_with("/v1/assets/batch") {
                        let entries: Value = serde_json::from_slice(&body).unwrap();
                        let out: Vec<Value> = entries
                            .as_array()
                            .unwrap()
                            .iter()
                            .map(|e| json!({"requestId": e["requestId"], "location": format!("https://fts.rbxcdn.com/{}?s=1", e["assetId"])}))
                            .collect();
                        let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
                        gz.write_all(serde_json::to_string(&out).unwrap().as_bytes()).unwrap();
                        let z = gz.finish().unwrap();
                        let mut head = format!("HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nTransfer-Encoding: chunked\r\n\r\n{:x}\r\n", z.len()).into_bytes();
                        head.extend_from_slice(&z);
                        head.extend_from_slice(b"\r\n0\r\n\r\n");
                        head
                    } else {
                        let b = format!("upstream {path}");
                        format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{b}", b.len()).into_bytes()
                    };
                    if w.write_all(&reply).await.is_err() {
                        return;
                    }
                }
            });
        }
    });
    (port, roots)
}

async fn echo_server() -> u16 {
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = l.local_addr().unwrap().port();
    tokio::spawn(async move {
        while let Ok((mut s, _)) = l.accept().await {
            tokio::spawn(async move {
                let mut buf = [0u8; 1024];
                while let Ok(n) = s.read(&mut buf).await {
                    if n == 0 || s.write_all(&buf[..n]).await.is_err() {
                        return;
                    }
                }
            });
        }
    });
    port
}

type Client = (BufReader<tokio::io::ReadHalf<tokio_rustls::client::TlsStream<TcpStream>>>, tokio::io::WriteHalf<tokio_rustls::client::TlsStream<TcpStream>>);

async fn connect_through(proxy_port: u16, host: &str, ca_pem: &str) -> Client {
    let mut tcp = TcpStream::connect(("127.0.0.1", proxy_port)).await.unwrap();
    tcp.write_all(format!("CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n").as_bytes()).await.unwrap();
    let mut head = Vec::new();
    while !head.ends_with(b"\r\n\r\n") {
        let mut b = [0u8; 1];
        tcp.read_exact(&mut b).await.unwrap();
        head.push(b[0]);
    }
    assert!(head.starts_with(b"HTTP/1.1 200"));
    let mut roots = rustls::RootCertStore::empty();
    let der = pem_der(ca_pem);
    roots.add(CertificateDer::from(der)).unwrap();
    let cfg = rustls::ClientConfig::builder_with_provider(provider())
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_root_certificates(roots)
        .with_no_client_auth();
    let tls = tokio_rustls::TlsConnector::from(Arc::new(cfg))
        .connect(ServerName::try_from(host.to_string()).unwrap(), tcp)
        .await
        .unwrap();
    let (r, w) = tokio::io::split(tls);
    (BufReader::new(r), w)
}

fn pem_der(pem: &str) -> Vec<u8> {
    let b64: String = pem.lines().filter(|l| !l.starts_with("-----")).collect();
    let mut out = Vec::new();
    let (mut acc, mut bits) = (0u32, 0);
    for c in b64.bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => continue,
        } as u32;
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    out
}

async fn request(c: &mut Client, method: &str, path: &str, body: &[u8]) -> (u16, Vec<u8>) {
    let head = format!("{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {}\r\n\r\n", body.len());
    c.1.write_all(head.as_bytes()).await.unwrap();
    c.1.write_all(body).await.unwrap();
    c.1.flush().await.unwrap();
    let resp = http1::read_head(&mut c.0).await.unwrap().unwrap();
    let raw = http1::read_body(&mut c.0, &resp, Some(method)).await.unwrap();
    let plain = http1::decode_content(&raw, resp.headers.get("content-encoding")).unwrap();
    (resp.status(), plain)
}

#[test]
fn proxy_end_to_end() {
    let rt = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().unwrap();
    let root = temp("proxy");
    let store = Arc::new(ConfigStore::open(root.clone()));
    let local = root.join("sky.png");
    std::fs::write(&local, b"LOCAL BYTES").unwrap();
    store
        .save_config(
            "Default",
            &json!({"replacement_rules": [
                {"name": "r", "replace_ids": ["1001"], "mode": "id", "with_id": 2002},
                {"name": "x", "replace_ids": ["3003"], "mode": "remove"},
                {"name": "f", "replace_ids": ["4004"], "mode": "local", "local_path": local.to_string_lossy()}
            ]}),
        )
        .unwrap();
    let ca = Arc::new(CertAuthority::open(&root.join("ca")).unwrap());
    let (up_port, roots) = rt.block_on(fake_roblox());
    let echo = rt.block_on(echo_server());
    let mut upstream = rustls::ClientConfig::builder_with_provider(provider())
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_root_certificates(roots)
        .with_no_client_auth();
    upstream.alpn_protocols = vec![b"http/1.1".to_vec()];
    let shared = Shared {
        config: store.clone(),
        ca: ca.clone(),
        router: Arc::new(AssetRouter::default()),
        upstream: Arc::new(upstream),
        resolve: Some(Arc::new(move |h: &str, p: u16| {
            if h == "tunnel.example" { ("127.0.0.1".into(), echo) } else { let _ = p; ("127.0.0.1".into(), up_port) }
        })),
    };
    let handle = proxy::start(rt.handle(), shared, 0).unwrap();
    let pem = ca.ca_pem();
    rt.block_on(async {
        // batch: replace + remove + local file
        let mut c = connect_through(handle.port, "assetdelivery.roblox.com", &pem).await;
        let batch = json!([
            {"requestId": "a", "assetId": 1001},
            {"requestId": "b", "assetId": 3003},
            {"requestId": "c", "assetId": 4004},
            {"requestId": "d", "assetId": 5005}
        ]);
        let (status, body) = request(&mut c, "POST", "/v1/assets/batch", batch.to_string().as_bytes()).await;
        assert_eq!(status, 200);
        let got: Value = serde_json::from_slice(&body).unwrap();
        let locs: Vec<&str> = got.as_array().unwrap().iter().map(|e| e["location"].as_str().unwrap()).collect();
        assert_eq!(locs, ["https://fts.rbxcdn.com/2002?s=1", "https://fts.rbxcdn.com/4004?s=1", "https://fts.rbxcdn.com/5005?s=1"]);
        // keep-alive on the same connection: legacy endpoint
        let (s, b) = request(&mut c, "GET", "/v1/asset/?id=1001", b"").await;
        assert_eq!((s, b.as_slice()), (200, &b"upstream /v1/asset/?id=2002"[..]));
        let (s, _) = request(&mut c, "GET", "/v1/asset/?id=3003", b"").await;
        assert_eq!(s, 404);

        // CDN download of the routed asset is answered from the local file
        let mut cdn = connect_through(handle.port, "fts.rbxcdn.com", &pem).await;
        let (s, b) = request(&mut cdn, "GET", "/4004?s=2", b"").await;
        assert_eq!((s, b.as_slice()), (200, &b"LOCAL BYTES"[..]));
        let (s, b) = request(&mut cdn, "GET", "/5005?s=1", b"").await;
        assert_eq!((s, b.as_slice()), (200, &b"upstream /5005?s=1"[..]));

        // any other host is a blind tunnel
        let mut t = TcpStream::connect(("127.0.0.1", handle.port)).await.unwrap();
        t.write_all(b"CONNECT tunnel.example:443 HTTP/1.1\r\n\r\nPING").await.unwrap();
        let mut buf = vec![0u8; 43];
        t.read_exact(&mut buf).await.unwrap();
        assert!(buf.starts_with(b"HTTP/1.1 200"));
        assert!(buf.ends_with(b"PING"));

        // plain requests that aren't proxy requests are refused
        let mut p = TcpStream::connect(("127.0.0.1", handle.port)).await.unwrap();
        p.write_all(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n").await.unwrap();
        let mut out = Vec::new();
        p.read_to_end(&mut out).await.unwrap();
        assert!(out.starts_with(b"HTTP/1.1 400"));
    });
    handle.stop();
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn ca_cannot_sign_other_sites() {
    // The CA's name constraints make clients reject anything outside roblox.com / rbxcdn.com.
    let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
    let root = temp("nc");
    let ca = CertAuthority::open(&root).unwrap();
    let cfg = ca.server_config("example.com").unwrap();
    let pem = ca.ca_pem();
    let ok = rt.block_on(async move {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = l.local_addr().unwrap().port();
        tokio::spawn(async move {
            let (s, _) = l.accept().await.unwrap();
            let _ = tokio_rustls::TlsAcceptor::from(cfg).accept(s).await;
        });
        let mut roots = rustls::RootCertStore::empty();
        roots.add(CertificateDer::from(pem_der(&pem))).unwrap();
        let ccfg = rustls::ClientConfig::builder_with_provider(provider())
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_root_certificates(roots)
            .with_no_client_auth();
        let tcp = TcpStream::connect(("127.0.0.1", port)).await.unwrap();
        tokio_rustls::TlsConnector::from(Arc::new(ccfg)).connect(ServerName::try_from("example.com").unwrap(), tcp).await.is_ok()
    });
    assert!(!ok, "a certificate for example.com must be rejected");
    let _ = std::fs::remove_dir_all(root);
}
