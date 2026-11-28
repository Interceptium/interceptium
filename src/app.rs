//! Glue between the proxy, the Roblox integration and the window.

use crate::certs::CertAuthority;
use crate::config::ConfigStore;
use crate::proxy::{self, ProxyHandle, Shared, LISTEN_HOST};
use crate::roblox::{self, LaunchWatcher, WatchContext};
use crate::router::AssetRouter;
use std::sync::{Arc, Mutex};

pub struct Controller {
    pub config: Arc<ConfigStore>,
    pub ca: Arc<CertAuthority>,
    pub router: Arc<AssetRouter>,
    rt: tokio::runtime::Runtime,
    handle: Mutex<Option<ProxyHandle>>,
    pub watcher: Arc<LaunchWatcher>,
}

impl Controller {
    pub fn new() -> Result<Arc<Controller>, String> {
        let config = Arc::new(ConfigStore::open(crate::paths::data_dir()));
        let ca = Arc::new(CertAuthority::open(&crate::paths::ca_dir())?);
        Self::with(config, ca)
    }

    pub fn with(config: Arc<ConfigStore>, ca: Arc<CertAuthority>) -> Result<Arc<Controller>, String> {
        let rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .thread_name("interceptium-proxy")
            .enable_all()
            .build()
            .map_err(|e| e.to_string())?;
        Ok(Arc::new(Controller {
            config,
            ca,
            router: Arc::new(AssetRouter::default()),
            rt,
            handle: Mutex::new(None),
            watcher: Arc::new(LaunchWatcher::default()),
        }))
    }

    fn handle(&self) -> std::sync::MutexGuard<'_, Option<ProxyHandle>> {
        self.handle.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn running(&self) -> bool {
        self.handle().is_some()
    }

    pub fn port(&self) -> u16 {
        self.handle().as_ref().map(|h| h.port).unwrap_or_else(|| self.config.settings().port)
    }

    pub fn proxy_url(&self) -> String {
        format!("http://{LISTEN_HOST}:{}", self.port())
    }

    pub fn start(self: &Arc<Self>) -> Result<u16, String> {
        if let Some(h) = self.handle().as_ref() {
            return Ok(h.port);
        }
        let shared = Shared {
            config: self.config.clone(),
            ca: self.ca.clone(),
            router: self.router.clone(),
            upstream: proxy::default_upstream(),
            resolve: None,
            cdn_cache: Default::default(),
        };
        let h = proxy::start(self.rt.handle(), shared, self.config.settings().port).map_err(|e| e.to_string())?;
        let port = h.port;
        *self.handle() = Some(h);
        roblox::install_ca(&self.ca.ca_pem());
        if self.config.settings().auto_capture_launches && (cfg!(windows) || cfg!(target_os = "macos")) {
            let me = Arc::downgrade(self);
            let me2 = me.clone();
            let me3 = me.clone();
            self.watcher.start(Arc::new(WatchContext {
                proxy_url: Box::new(move || me.upgrade().map(|c| c.proxy_url()).unwrap_or_default()),
                ca_pem: Box::new(move || me2.upgrade().map(|c| c.ca.ca_pem()).unwrap_or_default()),
                clear_first: Box::new(move || me3.upgrade().map(|c| c.config.settings().clear_cache_on_launch).unwrap_or(false)),
            }));
        }
        Ok(port)
    }

    pub fn stop(&self) {
        self.watcher.stop();
        if let Some(h) = self.handle().take() {
            h.stop();
        }
    }

    pub fn launch_roblox(self: &Arc<Self>) -> Result<(), String> {
        if !self.running() {
            self.start()?;
        }
        roblox::install_ca(&self.ca.ca_pem());
        if self.config.settings().clear_cache_on_launch {
            for p in roblox::list_players() {
                roblox::kill(p.pid);
            }
            roblox::clear_cache();
        }
        self.watcher.mark_ours(roblox::launch(&self.proxy_url(), &[], None));
        Ok(())
    }

    pub fn remove_from_roblox(&self) {
        roblox::uninstall_ca();
    }
}

impl Drop for Controller {
    fn drop(&mut self) {
        self.stop();
    }
}
