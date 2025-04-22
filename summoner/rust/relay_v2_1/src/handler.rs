use std::net::SocketAddr;
use tokio::net::TcpStream;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::broadcast;
use crate::state::ClientList;
use crate::logger::Logger;

pub async fn handle_client(
    stream: TcpStream,
    addr: SocketAddr,
    clients: ClientList,
    shutdown_rx: &mut broadcast::Receiver<()>,
    logger: Logger,
) -> Result<(), Box<dyn std::error::Error>> {
    let (reader, writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();
    let writer = std::sync::Arc::new(tokio::sync::Mutex::new(writer));

    {
        let mut list = clients.lock().await;
        list.push(writer.clone());
    }

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
        list.retain(|c| !std::sync::Arc::ptr_eq(c, &writer));
    }

    logger.info(&format!("{} connection closed.", addr));
    Ok(())
}
