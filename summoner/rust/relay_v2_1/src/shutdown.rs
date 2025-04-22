use tokio::sync::broadcast;
use tokio::signal;
use crate::logger::Logger;

pub fn spawn_shutdown_handler(shutdown_tx: broadcast::Sender<()>, logger: Logger) {
    tokio::spawn(async move {
        signal::ctrl_c().await.expect("Failed to listen for ctrl_c");
        logger.warn("🛑 Ctrl+C received — shutting down.");
        let _ = shutdown_tx.send(());
    });
}
