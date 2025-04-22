use pyo3::{prelude::*, types::PyModule, Bound};
use tokio::net::TcpListener;
use tokio::sync::broadcast;
use std::sync::Arc;
use tokio::sync::Mutex;

mod handler;
mod shutdown;
mod state;
mod logger;

use handler::handle_client;
use shutdown::spawn_shutdown_handler;
use state::ClientList;
use logger::Logger;

#[pyfunction]
pub fn start_tokio_server(_py: Python, host: String, port: u16) -> PyResult<()> {
    let rt = tokio::runtime::Runtime::new().unwrap();
    let logger = Logger::new("rust_server");

    rt.block_on(async {
        if let Err(e) = run_server(host, port, logger.clone()).await {
            logger.error(&format!("Rust server error: {}", e));
        }
    });

    Ok(())
}

async fn run_server(
    host: String,
    port: u16,
    logger: Logger,
) -> Result<(), Box<dyn std::error::Error>> {
    let addr = format!("{}:{}", host, port);
    let listener = TcpListener::bind(&addr).await?;
    logger.info(&format!("🚀 Rust server listening on {}", addr));

    let clients: ClientList = Arc::new(Mutex::new(Vec::new()));
    let (shutdown_tx, mut shutdown_rx) = broadcast::channel::<()>(1);
    spawn_shutdown_handler(shutdown_tx.clone(), logger.clone());

    loop {
        tokio::select! {
            Ok((stream, addr)) = listener.accept() => {
                logger.info(&format!("🔌 {} connected.", addr));
                let clients = Arc::clone(&clients);
                let mut shutdown_rx = shutdown_tx.subscribe();
                let logger = logger.clone();

                tokio::spawn(async move {
                    if let Err(e) = handle_client(stream, addr, clients, &mut shutdown_rx, logger.clone()).await {
                        logger.warn(&format!("⚠️ Error with {}: {}", addr, e));
                    }
                });
            }
            _ = shutdown_rx.recv() => {
                logger.info("🧹 Server received shutdown signal.");
                break;
            }
        }
    }

    Ok(())
}

#[pymodule]
fn relay_v2_1(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(start_tokio_server, m)?)?;
    Ok(())
}
