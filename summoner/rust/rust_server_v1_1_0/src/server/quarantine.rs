use std::collections::HashMap;
use std::net::SocketAddr;
use std::time::{Duration, Instant};
use std::sync::Arc;
use tokio::sync::Mutex;

use crate::logger::Logger;

// Tracks quarantined clients and how long they should be blocked.
#[derive(Debug)]
pub struct QuarantineList {
    quarantined: HashMap<SocketAddr, Instant>,
    cooldown: Duration,
}

impl QuarantineList {
    /// Create a new quarantine list with the given cooldown period.
    pub fn new(cooldown: Duration) -> Self {
        Self {
            quarantined: HashMap::new(),
            cooldown,
        }
    }

    /// Add a client to the quarantine list.
    pub fn insert(&mut self, addr: SocketAddr) {
        self.quarantined.insert(addr, Instant::now());
    }

    /// Returns true if the client is still quarantined.
    pub fn is_quarantined(&self, addr: &SocketAddr) -> bool {
        if let Some(&start) = self.quarantined.get(addr) {
            start.elapsed() < self.cooldown
        } else {
            false
        }
    }

    /// Clean up expired quarantine entries.
    pub fn cleanup(&mut self) {
        let now = Instant::now();
        self.quarantined
            .retain(|_, &mut t| now.duration_since(t) < self.cooldown);
    }

    pub fn start_background_cleanup(
        list: Arc<Mutex<Self>>,
        interval: Duration,
        logger: Logger,
    ) -> tokio::task::JoinHandle<()> {
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(interval);
            loop {
                ticker.tick().await;
                let mut q = list.lock().await;
                let before = q.quarantined.len();
                q.cleanup();
                let after = q.quarantined.len();
                if before != after {
                    logger.info(&format!("🧹 Quarantine cleanup: {} → {}", before, after));
                }
            }
        })
    }
}