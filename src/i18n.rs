//! Interface language. Interceptium is English; the Hidoceptium edition is Turkish.
//! Text is translated where it is drawn: `t()` looks up exact sentences first, then patterns
//! with `{}` placeholders (numbers, names and paths are kept as they are).

use std::borrow::Cow;

#[cfg(not(feature = "hido"))]
#[inline]
pub fn t(s: &str) -> Cow<'_, str> {
    Cow::Borrowed(s)
}

#[cfg(feature = "hido")]
pub fn t(s: &str) -> Cow<'_, str> {
    imp::translate(s)
}

#[cfg(feature = "hido")]
mod imp {
    use std::borrow::Cow;
    use std::collections::HashMap;
    use std::sync::{Mutex, OnceLock};

    const EXACT: &[(&str, &str)] = &[
        // main window
        ("Running", "Çalışıyor"),
        ("Stopped", "Durdu"),
        ("Working", "İşleniyor"),
        ("Proxy running", "Proxy çalışıyor"),
        ("Proxy off", "Proxy kapalı"),
        ("Launch Roblox", "Roblox'u başlat"),
        ("Clear cache", "Önbelleği temizle"),
        ("Configs", "Configler"),
        ("Import", "İçe aktar"),
        ("Import config files (.json); you can also drop them on the window", "Config dosyalarını (.json) içe aktar; pencereye sürükleyip bırakabilirsin de"),
        ("New config", "Yeni config"),
        ("No config", "Config yok"),
        ("Add rule", "Kural ekle"),
        ("Rename", "Yeniden adlandır"),
        ("Export this config to a file", "Bu config'i dosyaya aktar"),
        ("Delete config", "Config'i sil"),
        ("Search rules", "Kurallarda ara"),
        ("No results", "Sonuç yok"),
        ("No rules", "Kural yok"),
        ("No assets", "Varlık yok"),
        ("Activity", "Etkinlik"),
        ("Settings", "Ayarlar"),
        ("Guide", "Rehber"),
        ("Back", "Geri"),
        ("Edit", "Düzenle"),
        ("Delete", "Sil"),
        ("Rule", "Kural"),
        ("Group", "Grup"),
        ("Off in Settings", "Ayarlarda kapalı"),
        ("Replace", "Değiştir"),
        ("Remove", "Kaldır"),
        ("Local file", "Yerel dosya"),
        ("CDN", "CDN"),
        ("Config imported", "Config içe aktarıldı"),
        ("Config exported", "Config dışa aktarıldı"),
        ("Import configs", "Config'leri içe aktar"),
        ("Export config", "Config'i dışa aktar"),
        ("All files", "Tüm dosyalar"),
        ("Create", "Oluştur"),
        ("Rename config", "Config'i yeniden adlandır"),
        ("Name", "Ad"),
        ("Config name", "Config adı"),
        ("Delete rule", "Kuralı sil"),
        ("This rule", "Bu kural"),
        ("Close Roblox first.", "Önce Roblox'u kapat."),
        ("Roblox cache is already empty", "Roblox önbelleği zaten boş"),
        ("Roblox cache cleared", "Roblox önbelleği temizlendi"),
        ("Remove from Roblox", "Roblox'tan kaldır"),
        ("Hidoceptium's certificate and setting will be removed from every Roblox install. The proxy turns them back on when it starts.", "Hidoceptium'un sertifikası ve ayarı tüm Roblox kurulumlarından kaldırılacak. Proxy başlayınca bunları yeniden açar."),
        ("Repair", "Onar"),
        ("Reinstall", "Yeniden yükle"),
        ("Uninstall", "Kaldır"),
        ("Hidoceptium will close and its files will be restored.", "Hidoceptium kapanacak ve dosyaları geri yüklenecek."),
        ("Hidoceptium will close, be removed and installed again. Configs are kept.", "Hidoceptium kapanacak, kaldırılıp yeniden yüklenecek. Configler korunur."),
        ("Hidoceptium will close and open its uninstaller.", "Hidoceptium kapanacak ve kaldırıcısını açacak."),
        ("Turn on CDN mode?", "CDN modu açılsın mı?"),
        ("CDN rules download replacement files from web addresses written in your configs. When a game loads one of those assets, Hidoceptium connects to that address for you, so the server can see your IP address, and Hidoceptium can't check what the file contains. Only turn this on for configs you trust.\n\nRoblox itself never connects to these addresses. Downloads use HTTPS with certificate checks, are limited to 64 MB, and never go to this PC or your local network.",
         "CDN kuralları, config'lerinde yazan web adreslerinden yedek dosyalar indirir. Bir oyun bu varlıklardan birini yüklediğinde Hidoceptium o adrese senin yerine bağlanır; bu yüzden sunucu IP adresini görebilir ve Hidoceptium dosyanın içeriğini kontrol edemez. Bunu sadece güvendiğin config'ler için aç.\n\nRoblox bu adreslere hiçbir zaman kendisi bağlanmaz. İndirmeler sertifika kontrollü HTTPS kullanır, 64 MB ile sınırlıdır ve asla bu bilgisayara veya yerel ağına gitmez."),
        ("Turn on", "Aç"),
        ("CDN mode is on", "CDN modu açık"),
        ("CDN mode is off", "CDN modu kapalı"),
        ("Port saved", "Port kaydedildi"),
        ("Enter a port from 1024 to 65535.", "1024 ile 65535 arasında bir port gir."),
        ("Removed from Roblox", "Roblox'tan kaldırıldı"),
        ("Enter at least one asset ID or type.", "En az bir varlık kimliği veya türü gir."),
        ("Enter a numeric replacement asset ID.", "Sayısal bir yedek varlık kimliği gir."),
        ("Choose a file.", "Bir dosya seç."),
        ("File not found.", "Dosya bulunamadı."),
        ("Enter a web address.", "Bir web adresi gir."),
        ("Enter a full https:// address (no http, no user name or password).", "Tam bir https:// adresi gir (http, kullanıcı adı veya şifre olmadan)."),
        ("Enter a name.", "Bir ad gir."),
        ("The file is too large to be a config.", "Dosya bir config olamayacak kadar büyük."),
        ("This file is not a rule config.", "Bu dosya bir kural config'i değil."),
        ("not a JSON file", "JSON dosyası değil"),
        // settings
        ("General", "Genel"),
        ("Installation", "Kurulum"),
        ("Appearance", "Görünüm"),
        ("Window colours", "Pencere renkleri"),
        ("Dark", "Koyu"),
        ("Light", "Açık"),
        ("System", "Sistem"),
        ("Clear cache on launch", "Başlatırken önbelleği temizle"),
        ("Roblox loads replaced assets fresh", "Roblox değiştirilen varlıkları yeniden yükler"),
        ("Reroute website launches", "Web sitesinden açılan oyunları yönlendir"),
        ("Games started from the browser go through the proxy", "Tarayıcıdan başlatılan oyunlar proxy üzerinden geçer"),
        ("Port", "Port"),
        ("Local address the proxy listens on", "Proxy'nin dinlediği yerel adres"),
        ("Apply", "Uygula"),
        ("Roblox cache", "Roblox önbelleği"),
        ("Deletes downloaded assets so changes show up", "Değişikliklerin görünmesi için indirilen varlıkları siler"),
        ("Configs and settings", "Configler ve ayarlar"),
        ("Open folder", "Klasörü aç"),
        ("CDN mode", "CDN modu"),
        ("Lets CDN rules download replacement files from web addresses", "CDN kurallarının web adreslerinden yedek dosya indirmesine izin verir"),
        ("Certificate in Roblox", "Roblox'taki sertifika"),
        ("Takes Hidoceptium's changes out of Roblox", "Hidoceptium'un Roblox'taki değişikliklerini geri alır"),
        ("Version", "Sürüm"),
        ("Location", "Konum"),
        ("Not installed with Hidoceptium Setup", "Hidoceptium Kurulumu ile yüklenmedi"),
        // rule editor
        ("New rule", "Yeni kural"),
        ("Edit rule", "Kuralı düzenle"),
        ("Save", "Kaydet"),
        ("Cancel", "İptal"),
        ("Optional", "İsteğe bağlı"),
        ("Action", "İşlem"),
        ("Assets", "Varlıklar"),
        ("Asset ID or type, like Image", "Varlık kimliği veya türü (ör. Image)"),
        ("Add asset", "Varlık ekle"),
        ("File", "Dosya"),
        ("Web address", "Web adresi"),
        ("Replacement asset ID", "Yedek varlık kimliği"),
        ("Path to a file", "Dosya yolu"),
        ("Browse", "Gözat"),
        ("Choose file", "Dosya seç"),
        ("Asset ID", "Varlık kimliği"),
        ("CDN rules only run when CDN mode is on (Settings > Roblox).", "CDN kuralları yalnızca CDN modu açıkken çalışır (Ayarlar > Roblox)."),
        (".obj files are converted to Roblox meshes automatically.", ".obj dosyaları otomatik olarak Roblox mesh'ine dönüştürülür."),
        // window buttons
        ("Close", "Kapat"),
        ("Minimize", "Simge durumuna küçült"),
        ("Maximize", "Ekranı kapla"),
        ("Restore", "Önceki boyut"),
        // setup
        ("Hidoceptium Setup", "Hidoceptium Kurulumu"),
        ("Hidoceptium Update", "Hidoceptium Güncellemesi"),
        ("Install Hidoceptium", "Hidoceptium'u yükle"),
        ("Update Hidoceptium", "Hidoceptium'u güncelle"),
        ("Uninstall Hidoceptium", "Hidoceptium'u kaldır"),
        ("Installing Hidoceptium", "Hidoceptium yükleniyor"),
        ("Repairing Hidoceptium", "Hidoceptium onarılıyor"),
        ("Reinstalling Hidoceptium", "Hidoceptium yeniden yükleniyor"),
        ("Uninstalling Hidoceptium", "Hidoceptium kaldırılıyor"),
        ("Updating Hidoceptium", "Hidoceptium güncelleniyor"),
        ("Hidoceptium is installed", "Hidoceptium yüklendi"),
        ("Hidoceptium is repaired", "Hidoceptium onarıldı"),
        ("Hidoceptium is reinstalled", "Hidoceptium yeniden yüklendi"),
        ("Hidoceptium is uninstalled", "Hidoceptium kaldırıldı"),
        ("Hidoceptium is updated", "Hidoceptium güncellendi"),
        ("Hidoceptium is not installed.", "Hidoceptium yüklü değil."),
        ("Hidoceptium is not installed with its setup.", "Hidoceptium kurulumuyla yüklenmemiş."),
        ("Open Hidoceptium", "Hidoceptium'u aç"),
        ("Remove Hidoceptium from this PC", "Hidoceptium'u bu bilgisayardan kaldır"),
        ("Hidoceptium, its shortcuts and its changes to Roblox will be removed.", "Hidoceptium, kısayolları ve Roblox'taki değişiklikleri kaldırılacak."),
        ("Starting", "Başlıyor"),
        ("Choose a full folder path.", "Tam bir klasör yolu seç."),
        ("Folder", "Klasör"),
        ("Install location", "Yükleme konumu"),
        ("Desktop shortcut", "Masaüstü kısayolu"),
        ("Start menu shortcut", "Başlat menüsü kısayolu"),
        ("Install", "Yükle"),
        ("Restore missing or damaged files", "Eksik veya bozuk dosyaları geri yükle"),
        ("Remove and install again; configs are kept", "Kaldırıp yeniden yükle; config'ler korunur"),
        ("Kept as they are", "Olduğu gibi korunur"),
        ("Update", "Güncelle"),
        ("More options", "Diğer seçenekler"),
        ("Also delete configs and settings", "Config'leri ve ayarları da sil"),
        ("Something went wrong", "Bir şeyler ters gitti"),
        ("This setup file can't be read. Download it again.", "Bu kurulum dosyası okunamıyor. Yeniden indir."),
        ("Closing Hidoceptium", "Hidoceptium kapatılıyor"),
        ("Creating folder", "Klasör oluşturuluyor"),
        ("Copying Hidoceptium", "Hidoceptium kopyalanıyor"),
        ("Creating shortcuts", "Kısayollar oluşturuluyor"),
        ("Registering with Windows", "Windows'a kaydediliyor"),
        ("Done", "Tamamlandı"),
        ("Removing changes made to Roblox", "Roblox'taki değişiklikler kaldırılıyor"),
        ("Removing shortcuts", "Kısayollar kaldırılıyor"),
        ("Removing files", "Dosyalar kaldırılıyor"),
        ("Removing from Windows", "Windows'tan kaldırılıyor"),
        ("Removing configs and settings", "Configler ve ayarlar kaldırılıyor"),
        // log
        ("Proxy", "Proxy"),
        ("Roblox", "Roblox"),
        ("Watcher", "İzleyici"),
        ("Cache", "Önbellek"),
        ("Upstream", "Sunucu"),
        ("Error", "Hata"),
        ("Window", "Pencere"),
        ("not cleared: Roblox is running", "temizlenmedi: Roblox açık"),
        ("no Roblox Player install found", "Roblox Player kurulumu bulunamadı"),
        ("launched", "başlatıldı"),
        ("restarting Roblox through the proxy", "Roblox proxy üzerinden yeniden başlatılıyor"),
        ("stopped", "durduruldu"),
    ];

    /// `{}` marks a value that is kept (a number, name or path); values stay in the same order.
    const PATTERNS: &[(&str, &str)] = &[
        ("{} configs imported", "{} config içe aktarıldı"),
        ("Could not save: {}", "Kaydedilemedi: {}"),
        ("{}: not a .json file", "{}: .json dosyası değil"),
        ("\"{}\" will be permanently deleted.", "\"{}\" kalıcı olarak silinecek."),
        ("\"{}\" will be removed from this config.", "\"{}\" bu config'den kaldırılacak."),
        ("Proxy restarted on port {}", "Proxy {} portunda yeniden başlatıldı"),
        ("Version {}  ·  {}", "Sürüm {}  ·  {}"),
        ("Version {}  \u{2192}  {}", "Sürüm {}  \u{2192}  {}"),
        ("Version {}", "Sürüm {}"),
        ("Asset {}", "Varlık {}"),
        ("{} rules, {} enabled", "{} kural, {} etkin"),
        ("{} rule, {} enabled", "{} kural, {} etkin"),
        ("{} rules", "{} kural"),
        ("{} rule", "{} kural"),
        ("A newer version is installed. Repair or Reinstall puts back version {}.", "Daha yeni bir sürüm yüklü. Onar veya Yeniden yükle, {} sürümünü geri getirir."),
        ("A config named \"{}\" already exists.", "\"{}\" adında bir config zaten var."),
        ("{} is not a valid asset ID", "{} geçerli bir varlık kimliği değil"),
        ("{} is too small to be an asset ID", "{} bir varlık kimliği olamayacak kadar küçük"),
        ("\"{}\" is not an asset ID or asset type name", "\"{}\" bir varlık kimliği veya varlık türü adı değil"),
        ("Can't write to {}. Choose another folder.", "{} klasörüne yazılamıyor. Başka bir klasör seç."),
        ("Can't copy Hidoceptium: {}", "Hidoceptium kopyalanamadı: {}"),
        ("Can't create a shortcut: {}", "Kısayol oluşturulamadı: {}"),
        ("Can't remove {}: {}", "{} kaldırılamadı: {}"),
        ("Can't write {}: {}", "{} yazılamadı: {}"),
        ("asset {} removed", "{} varlığı kaldırıldı"),
        ("asset {} will be served from {}", "{} varlığı {} kaynağından verilecek"),
        ("asset {} served from {}", "{} varlığı {} kaynağından verildi"),
        ("asset {} -> {}", "{} varlığı \u{2192} {}"),
        ("could not prepare {}: {}", "{} hazırlanamadı: {}"),
        ("certificate set up in {} Roblox install(s)", "sertifika {} Roblox kurulumunda ayarlandı"),
        ("certificate removed from {} Roblox install(s)", "sertifika {} Roblox kurulumundan kaldırıldı"),
        ("could not delete {}: {}", "{} silinemedi: {}"),
        ("Roblox cache cleared ({} file(s))", "Roblox önbelleği temizlendi ({} dosya)"),
        ("launch failed: {}", "başlatılamadı: {}"),
        ("could not open a window: {}", "pencere açılamadı: {}"),
        ("listening on {}", "{} adresinde dinleniyor"),
        ("could not download from {}: {}", "{} adresinden indirilemedi: {}"),
        ("{} can't be converted to a Roblox mesh: {}", "{} Roblox mesh'ine dönüştürülemedi: {}"),
        ("missing local file {}", "yerel dosya bulunamadı: {}"),
    ];

    struct Tables {
        exact: HashMap<&'static str, &'static str>,
        patterns: Vec<(Vec<&'static str>, &'static str)>,
    }

    fn tables() -> &'static Tables {
        static T: OnceLock<Tables> = OnceLock::new();
        T.get_or_init(|| {
            let mut patterns: Vec<(Vec<&'static str>, &'static str)> =
                PATTERNS.iter().map(|(en, tr)| (en.split("{}").collect(), *tr)).collect();
            // most specific (most fixed text) first
            patterns.sort_by_key(|(parts, _)| std::cmp::Reverse(parts.iter().map(|p| p.len()).sum::<usize>()));
            Tables { exact: EXACT.iter().copied().collect(), patterns }
        })
    }

    fn match_pattern<'a>(parts: &[&str], s: &'a str) -> Option<Vec<&'a str>> {
        let mut rest = s.strip_prefix(parts[0])?;
        let mut values = Vec::new();
        for (i, part) in parts.iter().enumerate().skip(1) {
            let last = i == parts.len() - 1;
            let value = if last {
                let v = if part.is_empty() { rest } else { rest.strip_suffix(part)? };
                rest = "";
                v
            } else {
                let at = rest.find(part)?;
                let v = &rest[..at];
                rest = &rest[at + part.len()..];
                v
            };
            if value.is_empty() {
                return None;
            }
            values.push(value);
        }
        rest.is_empty().then_some(values)
    }

    fn fill(template: &str, values: &[&str]) -> String {
        let mut out = String::new();
        let mut it = values.iter();
        for (i, piece) in template.split("{}").enumerate() {
            if i > 0 {
                out.push_str(it.next().copied().unwrap_or(""));
            }
            out.push_str(piece);
        }
        out
    }

    pub fn translate(s: &str) -> Cow<'_, str> {
        let t = tables();
        if let Some(tr) = t.exact.get(s) {
            return Cow::Borrowed(tr);
        }
        if s.is_empty() || !s.bytes().any(|b| b.is_ascii_alphabetic()) {
            return Cow::Borrowed(s);
        }
        static CACHE: OnceLock<Mutex<HashMap<String, Option<String>>>> = OnceLock::new();
        let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
        if let Some(hit) = cache.lock().unwrap_or_else(|e| e.into_inner()).get(s) {
            return hit.clone().map(Cow::Owned).unwrap_or(Cow::Borrowed(s));
        }
        let found = t.patterns.iter().find_map(|(parts, tr)| match_pattern(parts, s).map(|v| fill(tr, &v)));
        let mut c = cache.lock().unwrap_or_else(|e| e.into_inner());
        if c.len() > 4096 {
            c.clear();
        }
        c.insert(s.to_string(), found.clone());
        found.map(Cow::Owned).unwrap_or(Cow::Borrowed(s))
    }

    #[cfg(test)]
    mod tests {
        use super::translate;

        #[test]
        fn exact_and_patterns() {
            assert_eq!(translate("Add rule"), "Kural ekle");
            assert_eq!(translate("4 rules, 3 enabled"), "4 kural, 3 etkin");
            assert_eq!(translate("1 rule"), "1 kural");
            assert_eq!(translate("Version 1.0.0  ·  C:\\x"), "Sürüm 1.0.0  ·  C:\\x");
            assert_eq!(translate("Version 1.0.0"), "Sürüm 1.0.0");
            assert_eq!(translate("asset 123 -> 456"), "123 varlığı \u{2192} 456");
            assert_eq!(translate("asset 123 removed"), "123 varlığı kaldırıldı");
            assert_eq!(translate("My Sky Pack"), "My Sky Pack", "names stay as they are");
            assert_eq!(translate("1012345678"), "1012345678");
        }
    }
}
