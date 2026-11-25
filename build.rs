// Embeds the icon, version information and application manifest into the Windows exe.
fn main() {
    println!("cargo:rerun-if-changed=assets/logo.ico");
    println!("cargo:rerun-if-changed=windows/app.manifest");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let mut res = winresource::WindowsResource::new();
        res.set_icon("assets/logo.ico")
            .set_manifest_file("windows/app.manifest")
            .set("ProductName", "Interceptium")
            .set("FileDescription", "Interceptium")
            .set("CompanyName", "Interceptium")
            .set("LegalCopyright", "MIT License")
            .set("OriginalFilename", "Interceptium.exe")
            .set("InternalName", "Interceptium");
        if let Err(e) = res.compile() {
            panic!("could not embed Windows resources: {e}");
        }
    }
}
