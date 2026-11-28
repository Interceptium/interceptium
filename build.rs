// Embeds the icon, version information and application manifest into the Windows exe.
// `--features hido` builds the Hidoceptium edition with its own name, icon and version.
fn main() {
    println!("cargo:rerun-if-changed=assets/logo.ico");
    println!("cargo:rerun-if-changed=assets/hido/logo.ico");
    println!("cargo:rerun-if-changed=windows/app.manifest");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let hido = std::env::var_os("CARGO_FEATURE_HIDO").is_some();
        let (name, icon, version) = if hido {
            ("Hidoceptium", "assets/hido/logo.ico", "1.0.0".to_string())
        } else {
            ("Interceptium", "assets/logo.ico", std::env::var("CARGO_PKG_VERSION").unwrap_or_default())
        };
        let mut parts = version.split('.').map(|p| p.parse::<u64>().unwrap_or(0));
        let packed = (parts.next().unwrap_or(0) << 48) | (parts.next().unwrap_or(0) << 32) | (parts.next().unwrap_or(0) << 16);
        let mut res = winresource::WindowsResource::new();
        res.set_icon(icon)
            .set_manifest_file("windows/app.manifest")
            .set("ProductName", name)
            .set("FileDescription", name)
            .set("CompanyName", name)
            .set("LegalCopyright", "MIT License")
            .set("OriginalFilename", &format!("{name}.exe"))
            .set("InternalName", name)
            .set("FileVersion", &version)
            .set("ProductVersion", &version)
            .set_version_info(winresource::VersionInfo::FILEVERSION, packed)
            .set_version_info(winresource::VersionInfo::PRODUCTVERSION, packed);
        if let Err(e) = res.compile() {
            panic!("could not embed Windows resources: {e}");
        }
    }
}
