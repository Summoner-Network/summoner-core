use pyo3::{prelude::*, types::PyModule, Bound};
use tokio::net::{TcpListener, TcpStream};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::broadcast;
use tokio::signal;
use std::net::SocketAddr;
use std::sync::Arc;
use tokio::sync::Mutex;

type Client = Arc<Mutex<tokio::net::tcp::OwnedWriteHalf>>;
type ClientList = Arc<Mutex<Vec<Client>>>;

#[pyfunction]
pub fn start_tokio_server(_py: Python, host: String, port: u16) -> PyResult<()> {
    // ✅ Persistently blocks Python by running the server directly
    let rt = tokio::runtime::Runtime::new().unwrap();
    rt.block_on(async {
        if let Err(e) = run_server(host, port).await {
            eprintln!("Rust server error: {}", e);
        }
    });

    Ok(())
}


async fn run_server(host: String, port: u16) -> Result<(), Box<dyn std::error::Error>> {
    let addr = format!("{}:{}", host, port);
    let listener = TcpListener::bind(&addr).await?;
    println!("🚀 Rust server listening on {}", addr);

    let clients: ClientList = Arc::new(Mutex::new(Vec::new()));
    let (shutdown_tx, mut shutdown_rx) = broadcast::channel::<()>(1);

    // Graceful shutdown
    let _shutdown_signal = {
        let shutdown_tx = shutdown_tx.clone();
        tokio::spawn(async move {
            signal::ctrl_c().await.expect("Failed to listen for ctrl_c");
            println!("\n🛑 Ctrl+C received — shutting down.");
            let _ = shutdown_tx.send(());
        })
    };

    loop {
        tokio::select! {
            Ok((stream, addr)) = listener.accept() => {
                println!("🔌 {} connected.", addr);
                let clients = Arc::clone(&clients);
                let mut shutdown_rx = shutdown_tx.subscribe();

                tokio::spawn(async move {
                    if let Err(e) = handle_client(stream, addr, clients, &mut shutdown_rx).await {
                        println!("⚠️ Error with {}: {}", addr, e);
                    }
                });
            }
            _ = shutdown_rx.recv() => {
                println!("🧹 Server received shutdown signal.");
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
) -> Result<(), Box<dyn std::error::Error>> {
    let (reader, writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();
    let writer = Arc::new(Mutex::new(writer));

    {
        let mut list = clients.lock().await;
        list.push(writer.clone());
    }

    loop {
        tokio::select! {
            maybe_line = lines.next_line() => {
                match maybe_line {
                    Ok(Some(line)) => {
                        println!("📨 From {}: {}", addr, line.trim());
                        let msg = format!("[{}] {}\n", addr, line.trim());

                        let others = clients.lock().await;
                        for client in others.iter() {
                            let mut w = client.lock().await;
                            if let Err(e) = w.write_all(msg.as_bytes()).await {
                                eprintln!("❌ Failed to send to client {}: {}", addr, e);
                            }
                        }
                    }
                    Ok(None) => {
                        println!("⚠️ {} disconnected gracefully.", addr);
                        break;
                    }
                    Err(e) => {
                        println!("❌ Error reading from {}: {}", addr, e);
                        break;
                    }
                }
            }
            _ = shutdown_rx.recv() => {
                println!("🛑 {} disconnected due to shutdown.", addr);
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

    Ok(())
}

#[pymodule]
fn relay_v2(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(start_tokio_server, m)?)?;
    Ok(())
}
