//! Local certificate authority.
//!
//! * The CA is made on this computer; its private key stays in the per-user data folder
//!   (mode 0600 on macOS/Linux).
//! * It carries an X.509 Name Constraints extension permitting only the Roblox domains below,
//!   so TLS libraries reject any certificate it would sign for other sites.
//! * It is only added to Roblox's private `ssl/cacert.pem`, never to the system trust store.

use rcgen::{
    BasicConstraints, CertificateParams, DistinguishedName, DnType, ExtendedKeyUsagePurpose, GeneralSubtree, IsCa,
    Issuer, KeyPair, KeyUsagePurpose, NameConstraints, SerialNumber,
};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

pub const CA_COMMON_NAME: &str = concat!(crate::brand_name!(), " Local CA");
pub const PERMITTED_DOMAINS: [&str; 2] = ["roblox.com", "rbxcdn.com"];

struct Ca {
    cert_pem: String,
    cert_der: CertificateDer<'static>,
    issuer: Issuer<'static, KeyPair>,
}

pub struct CertAuthority {
    #[allow(dead_code)]
    pub cert_path: PathBuf,
    #[allow(dead_code)]
    pub key_path: PathBuf,
    ca: Mutex<Arc<Ca>>,
    contexts: Mutex<HashMap<String, Arc<rustls::ServerConfig>>>,
}

pub fn provider() -> Arc<rustls::crypto::CryptoProvider> {
    Arc::new(rustls::crypto::ring::default_provider())
}

fn date(offset_days: i64) -> time::OffsetDateTime {
    let d = chrono::Utc::now().date_naive() + chrono::Duration::days(offset_days);
    use chrono::Datelike;
    rcgen::date_time_ymd(d.year(), d.month() as u8, d.day() as u8)
}

fn random_serial() -> SerialNumber {
    use ring::rand::SecureRandom;
    let mut bytes = [0u8; 16];
    let _ = ring::rand::SystemRandom::new().fill(&mut bytes);
    bytes[0] &= 0x7f; // keep it positive
    bytes[0] |= 0x01;
    SerialNumber::from_slice(&bytes)
}

fn write_private(path: &Path, data: &[u8]) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let mut f = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(path)?;
        f.write_all(data)
    }
    #[cfg(not(unix))]
    {
        std::fs::write(path, data)
    }
}

fn pem_to_der(pem: &str) -> Option<Vec<u8>> {
    let body: String = pem
        .lines()
        .skip_while(|l| !l.starts_with("-----BEGIN CERTIFICATE-----"))
        .skip(1)
        .take_while(|l| !l.starts_with("-----END"))
        .collect();
    base64_decode(body.trim())
}

fn base64_decode(s: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(s.len() * 3 / 4);
    let mut acc = 0u32;
    let mut bits = 0;
    for c in s.bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            b'=' => break,
            b'\r' | b'\n' | b' ' => continue,
            _ => return None,
        } as u32;
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    Some(out)
}

/// Days until the certificate expires, or None if it can't be read.
fn days_left(der: &[u8]) -> Option<i64> {
    let (_, cert) = x509_parser::parse_x509_certificate(der).ok()?;
    let end = cert.validity().not_after.timestamp();
    Some((end - chrono::Utc::now().timestamp()) / 86400)
}

impl CertAuthority {
    pub fn open(directory: &Path) -> Result<CertAuthority, String> {
        let _ = std::fs::create_dir_all(directory);
        let cert_path = directory.join(concat!(crate::brand_lower!(), "-ca.pem"));
        let key_path = directory.join(concat!(crate::brand_lower!(), "-ca.key"));
        let ca = match load(&cert_path, &key_path) {
            Some(ca) => ca,
            None => create(&cert_path, &key_path)?,
        };
        Ok(CertAuthority { cert_path, key_path, ca: Mutex::new(Arc::new(ca)), contexts: Mutex::new(HashMap::new()) })
    }

    fn current(&self) -> Arc<Ca> {
        self.ca.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn ca_pem(&self) -> String {
        self.current().cert_pem.clone()
    }

    /// TLS server settings presenting a certificate for `host` (cached in memory).
    pub fn server_config(&self, host: &str) -> Result<Arc<rustls::ServerConfig>, String> {
        let host = host.to_ascii_lowercase();
        if let Some(c) = self.contexts.lock().unwrap_or_else(|e| e.into_inner()).get(&host) {
            return Ok(c.clone());
        }
        let ca = self.current();
        let key = KeyPair::generate().map_err(|e| e.to_string())?;
        let mut params = CertificateParams::new(vec![host.clone()]).map_err(|e| e.to_string())?;
        let mut dn = DistinguishedName::new();
        dn.push(DnType::CommonName, host.as_str());
        params.distinguished_name = dn;
        params.is_ca = IsCa::ExplicitNoCa;
        params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
        params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
        params.use_authority_key_identifier_extension = true;
        params.not_before = date(-1);
        params.not_after = date(397);
        params.serial_number = Some(random_serial());
        let cert = params.signed_by(&key, &ca.issuer).map_err(|e| e.to_string())?;
        let chain = vec![cert.der().clone(), ca.cert_der.clone()];
        let key_der = PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der()));
        let mut config = rustls::ServerConfig::builder_with_provider(provider())
            .with_safe_default_protocol_versions()
            .map_err(|e| e.to_string())?
            .with_no_client_auth()
            .with_single_cert(chain, key_der)
            .map_err(|e| e.to_string())?;
        config.alpn_protocols = vec![b"http/1.1".to_vec()];
        let config = Arc::new(config);
        self.contexts.lock().unwrap_or_else(|e| e.into_inner()).insert(host, config.clone());
        Ok(config)
    }
}

fn load(cert_path: &Path, key_path: &Path) -> Option<Ca> {
    let cert_pem = std::fs::read_to_string(cert_path).ok()?;
    let key_pem = std::fs::read_to_string(key_path).ok()?;
    let der = pem_to_der(&cert_pem)?;
    if days_left(&der)? <= 30 {
        return None;
    }
    let key = KeyPair::from_pem(&key_pem).ok()?;
    let issuer = Issuer::from_ca_cert_pem(&cert_pem, key).ok()?;
    Some(Ca { cert_pem, cert_der: CertificateDer::from(der), issuer })
}

fn create(cert_path: &Path, key_path: &Path) -> Result<Ca, String> {
    let key = KeyPair::generate().map_err(|e| e.to_string())?;
    let mut params = CertificateParams::new(Vec::<String>::new()).map_err(|e| e.to_string())?;
    let mut dn = DistinguishedName::new();
    dn.push(DnType::CommonName, CA_COMMON_NAME);
    dn.push(DnType::OrganizationName, concat!(crate::brand_name!(), " (this computer only)"));
    params.distinguished_name = dn;
    params.is_ca = IsCa::Ca(BasicConstraints::Constrained(0));
    params.name_constraints = Some(NameConstraints {
        permitted_subtrees: PERMITTED_DOMAINS.iter().map(|d| GeneralSubtree::DnsName(d.to_string())).collect(),
        excluded_subtrees: vec![],
    });
    params.key_usages = vec![KeyUsagePurpose::DigitalSignature, KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
    params.not_before = date(-1);
    params.not_after = date(825);
    params.serial_number = Some(random_serial());
    let cert = params.self_signed(&key).map_err(|e| e.to_string())?;
    let cert_pem = cert.pem();
    write_private(key_path, key.serialize_pem().as_bytes()).map_err(|e| e.to_string())?;
    std::fs::write(cert_path, &cert_pem).map_err(|e| e.to_string())?;
    let cert_der = cert.der().clone();
    Ok(Ca { cert_pem, cert_der, issuer: Issuer::new(params, key) })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn creates_reloads_and_constrains() {
        let dir = std::env::temp_dir().join(format!("icert-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let ca = CertAuthority::open(&dir).unwrap();
        let pem = ca.ca_pem();
        assert!(pem.contains("BEGIN CERTIFICATE"));
        let again = CertAuthority::open(&dir).unwrap();
        assert_eq!(again.ca_pem(), pem, "an existing CA is reused");
        let der = pem_to_der(&pem).unwrap();
        let (_, cert) = x509_parser::parse_x509_certificate(&der).unwrap();
        let nc = cert.name_constraints().unwrap().unwrap().value;
        let permitted: Vec<String> = nc
            .permitted_subtrees
            .as_ref()
            .unwrap()
            .iter()
            .map(|s| format!("{}", s.base))
            .collect();
        assert_eq!(permitted.len(), 2);
        assert!(permitted.iter().any(|p| p.contains("roblox.com")));
        assert!(ca.server_config("assetdelivery.roblox.com").is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
