use pyo3::prelude::*;
use tokio::net::{TcpListener, TcpStream};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use std::sync::Arc;
use tokio::sync::Mutex;
use tokio::sync::broadcast;
use tokio::signal;

type Writer = tokio::net::tcp::OwnedWriteHalf;

/// Start the async TCP server from Python.
#[pyfunction]
pub fn start_tokio_server(_py: Python, host: String, port: u16) -> PyResult<()> {
    // BLOCKING call: this will run until manually stopped (Ctrl+C or external signal)
    let rt = tokio::runtime::Runtime::new().unwrap();
    rt.block_on(run_server(host, port));
    Ok(())
}

// pub fn start_tokio_server(_py: Python, host: String, port: u16) -> PyResult<()> {
//     std::thread::spawn(move || {
//         let rt = tokio::runtime::Runtime::new().unwrap();
//         rt.block_on(run_server(host, port));
//     });

//     Ok(())
// }


async fn run_server(host: String, port: u16) {
    let listener = TcpListener::bind((host.as_str(), port)).await.unwrap();
    println!("🚀 Rust server listening on {}:{}", host, port);

    let clients: Arc<Mutex<Vec<Arc<Mutex<Writer>>>>> = Arc::new(Mutex::new(Vec::new()));

    // Used to notify tasks to shut down gracefully
    let (shutdown_tx, _) = broadcast::channel::<()>(1);

    tokio::select! {
        _ = async {
            loop {
                let (socket, addr) = listener.accept().await.unwrap();
                println!("🔌 Connection from {:?}", addr);

                let clients = Arc::clone(&clients);
                let mut shutdown_rx = shutdown_tx.subscribe();

                tokio::spawn(async move {
                    tokio::select! {
                        _ = handle_client(socket, clients) => {}
                        _ = shutdown_rx.recv() => {
                            println!("🛑 Client handler received shutdown");
                        }
                    }
                });
            }
        } => {}

        _ = signal::ctrl_c() => {
            println!("\n🛑 Received Ctrl+C, shutting down server.");
        }
    }

    // Optional: notify all client handlers to shut down
    let _ = shutdown_tx.send(());
}

async fn handle_client(stream: TcpStream, clients: Arc<Mutex<Vec<Arc<Mutex<Writer>>>>>) {
    let (reader, writer) = stream.into_split();
    let mut reader = BufReader::new(reader).lines();

    let writer = Arc::new(Mutex::new(writer));
    {
        let mut c = clients.lock().await;
        c.push(writer.clone());
    }

    while let Ok(Some(line)) = reader.next_line().await {
        println!("📨 Received: {}", line);
        let msg = format!("{}\n", line);

        let c = clients.lock().await;
        for w in c.iter() {
            let mut w = w.lock().await;
            if let Err(e) = w.write_all(msg.as_bytes()).await {
                eprintln!("❌ Failed to send to client: {:?}", e);
            }
        }
    }

    println!("⚠️ Client disconnected");
}

/// Expose the module to Python
#[pymodule]
fn relay_v1(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(start_tokio_server, m)?)?;
    Ok(())
}
