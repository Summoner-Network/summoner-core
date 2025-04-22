// src/server.rs
use std::net::SocketAddr;
use std::sync::Arc;
use tokio::net::{TcpListener, TcpStream};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{Mutex, broadcast, mpsc};

use crate::logger::Logger;

pub type Client = Arc<Mutex<tokio::net::tcp::OwnedWriteHalf>>;
pub type ClientList = Arc<Mutex<Vec<Client>>>;

pub async fn run_server(host: String, port: u16, logger: Logger) -> Result<(), Box<dyn std::error::Error>> {
    let addr = format!("{}:{}", host, port);
    let listener = TcpListener::bind(&addr).await?;
    logger.info(&format!("🚀 Rust server listening on {}", addr));

    let clients: ClientList = Arc::new(Mutex::new(Vec::new()));
    let (shutdown_tx, mut shutdown_rx) = broadcast::channel::<()>(1);

    {
        let shutdown_tx = shutdown_tx.clone();
        let shutdown_logger = logger.clone();
        tokio::spawn(async move {
            tokio::signal::ctrl_c().await.expect("Failed to listen for ctrl_c");
            shutdown_logger.warn("🛑 Ctrl+C received — shutting down.");
            let _ = shutdown_tx.send(());
        });
    }

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

async fn handle_client(
    stream: TcpStream,
    addr: SocketAddr,
    clients: ClientList,
    shutdown_rx: &mut broadcast::Receiver<()>,
    logger: Logger,
) -> Result<(), Box<dyn std::error::Error>> {
    let (reader, writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();
    let writer = Arc::new(Mutex::new(writer));

    {
        let mut list = clients.lock().await;
        list.push(writer.clone());
    }

    let (limit_tx, _limit_rx) = mpsc::channel::<()>(100); // Optional backpressure

    loop {
        tokio::select! {
            maybe_line = lines.next_line() => {
                match maybe_line {
                    Ok(Some(line)) => {
                        logger.info(&format!("📨 From {}: {}", addr, line.trim()));
                        let msg = format!("[{}] {}\n", addr, line.trim());

                        let others = clients.lock().await;
                        for client in others.iter() {
                            let mut w = client.lock().await;
                            if let Err(e) = w.write_all(msg.as_bytes()).await {
                                logger.warn(&format!("❌ Failed to send to client {}: {}", addr, e));
                            }
                        }

                        if limit_tx.try_send(()).is_err() {
                            logger.warn("⚠️ Backpressure: dropping message due to slow consumers.");
                        }
                    }
                    Ok(None) => {
                        logger.info(&format!("⚠️ {} disconnected gracefully.", addr));
                        break;
                    }
                    Err(e) => {
                        logger.warn(&format!("❌ Error reading from {}: {}", addr, e));
                        break;
                    }
                }
            }
            _ = shutdown_rx.recv() => {
                logger.warn(&format!("🛑 {} disconnected due to shutdown.", addr));
                let mut w = writer.lock().await;
                let _ = w.write_all(b"Warning: Server is shutting down.\n").await;
                break;
            }
        }
    }

    {
        let mut list = clients.lock().await;
        list.retain(|c| !Arc::ptr_eq(c, &writer));
    }

    logger.info(&format!("{} connection closed.", addr));
    Ok(())
}
