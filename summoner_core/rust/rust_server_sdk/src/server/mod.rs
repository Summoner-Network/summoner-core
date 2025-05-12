// Standard library import for network socket addresses (e.g., IP:port)
use std::net::SocketAddr;

// Arc = shared ownership; Mutex = safe shared mutation
use std::sync::Arc;

// Import TCP-related tools from the async runtime Tokio
use tokio::net::{TcpListener, TcpStream};

// Import buffered reading and writing utilities, like `reader.readline()` in Python
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines};

// Import synchronization tools: broadcast = one-to-many signaling, mpsc = message passing
use tokio::sync::{Mutex, broadcast, mpsc};

// Import a custom logging module defined elsewhere
use crate::logger::Logger;

// Define a type alias for a single client's writable stream, wrapped for concurrency
pub type Client = Arc<Mutex<tokio::net::tcp::OwnedWriteHalf>>;

// Define a type alias for the shared list of all clients
pub type ClientList = Arc<Mutex<Vec<Client>>>;

/// This function launches the main server loop:
/// - Binds to the provided host and port
/// - Sets up shared state and shutdown signaling
/// - Delegates to the connection handling loop
pub async fn run_server(host: String, port: u16, logger: Logger) -> Result<(), Box<dyn std::error::Error>> {
    let addr = format!("{}:{}", host, port);

    // Bind a TCP socket to the address
    let listener = TcpListener::bind(&addr).await?;

    // Log the startup message
    logger.info(&format!("🚀 Rust server listening on {}", addr));

    // Create a shared, thread-safe vector of clients
    let clients: ClientList = Arc::new(Mutex::new(Vec::new()));

    // Set up a broadcast channel for shutdown signaling
    let (shutdown_tx, shutdown_rx) = broadcast::channel::<()>(1);

    // Spawn a task to listen for Ctrl+C (SIGINT) and trigger shutdown
    spawn_shutdown_listener(shutdown_tx.clone(), logger.clone());

    // Begin accepting and handling client connections
    accept_connections(listener, clients, shutdown_tx, shutdown_rx, logger).await;

    // Return success
    Ok(())
}

/// This function handles incoming client connections:
/// - Listens for new TCP clients
/// - Spawns a task for each connected client
/// - Reacts to global shutdown signal
async fn accept_connections(
    listener: TcpListener,
    clients: ClientList,
    shutdown_tx: broadcast::Sender<()>,
    mut shutdown_rx: broadcast::Receiver<()>,
    logger: Logger,
) {
    loop {
        tokio::select! {
            // Accept a new client connection
            Ok((stream, addr)) = listener.accept() => {
                logger.info(&format!("🔌 {} connected.", addr));

                // Clone required context into the new task
                let logger = logger.clone();
                let clients = clients.clone();
                let mut shutdown_rx = shutdown_tx.subscribe();

                // Spawn a new async task for the client session
                tokio::spawn(async move {
                    if let Err(e) = handle_connection(stream, addr, clients, &mut shutdown_rx, &logger).await {
                        logger.warn(&format!("⚠️ Error with {}: {}", addr, e));
                    }
                });
            }

            // If a global shutdown signal is received
            _ = shutdown_rx.recv() => {
                logger.info("🧹 Server received shutdown signal.");
                break;
            }
        }
    }
}

/// This function runs the session for a single client:
/// - Splits the incoming TCP stream into read/write halves
/// - Wraps the writable half in Arc<Mutex> so it can be safely shared
/// - Adds the writer to the global client list for broadcasting
/// - Calls `message_loop` to handle incoming messages
/// - Removes the client from the list when the session ends
async fn handle_connection(
    stream: TcpStream,
    addr: SocketAddr,
    clients: ClientList,
    shutdown_rx: &mut broadcast::Receiver<()>,
    logger: &Logger,
) -> Result<(), Box<dyn std::error::Error>> {
    // Separate the stream into reading and writing halves
    let (reader_half, writer_half) = stream.into_split();

    // Wrap the read half to allow reading lines with `.next_line()`
    let mut reader = BufReader::new(reader_half).lines();

    // Wrap the write half in a Mutex so multiple tasks can access it safely
    let writer = Arc::new(Mutex::new(writer_half));

    // Add this client to the shared list so it can receive broadcasts
    {
        let mut list = clients.lock().await;
        list.push(writer.clone());
    }

    // Run the message loop for this client
    message_loop(&mut reader, &clients, &writer, addr, shutdown_rx, logger).await;

    // After the client disconnects, remove it from the list
    {
        let mut list = clients.lock().await;
        list.retain(|c| !Arc::ptr_eq(c, &writer)); // only retain if not this writer
    }

    // Log that the connection has ended
    logger.info(&format!("{} connection closed.", addr));

    // Return success
    Ok(())
}

/// This function handles all incoming messages from a single client:
/// - Waits for new lines from the client
/// - Logs and formats the message
/// - Broadcasts it to all other clients (except the sender)
/// - Handles shutdown signals and notifies the client of shutdown
async fn message_loop(
    reader: &mut Lines<BufReader<tokio::net::tcp::OwnedReadHalf>>,
    clients: &ClientList,
    sender: &Client,
    addr: SocketAddr,
    shutdown_rx: &mut broadcast::Receiver<()>,
    logger: &Logger,
) {
    // Optional channel to simulate detection of slow clients (backpressure),
    // not used in this implementation but demonstrates a monitoring hook
    let (limit_tx, _limit_rx) = mpsc::channel::<()>(100);

    // Loop to handle input or shutdown, whichever happens first
    loop {
        tokio::select! {
            // Try reading a new line from the client
            maybe_line = reader.next_line() => {
                match maybe_line {
                    // A valid line was received
                    Ok(Some(line)) => {
                        // Log the message with its source address
                        logger.info(&format!("📨 From {}: {}", addr, line.trim()));

                        // Format the message with sender's address prefix
                        let msg = format!("[{}] {}\n", addr, line.trim());

                        // Lock the client list to broadcast to all others
                        let others = clients.lock().await;
                        for client in others.iter() {
                            // Don't send the message back to the sender
                            if Arc::ptr_eq(sender, client) {
                                continue;
                            }

                            // Lock each client's writer and send the message
                            let mut w = client.lock().await;
                            if let Err(e) = w.write_all(msg.as_bytes()).await {
                                logger.warn(&format!("❌ Failed to send to client {}: {}", addr, e));
                            }
                        }

                        // Try to send a token into the channel to simulate flow control
                        if limit_tx.try_send(()).is_err() {
                            logger.warn("⚠️ Backpressure: dropping message due to slow consumers.");
                        }
                    }

                    // The client closed the connection cleanly
                    Ok(None) => {
                        logger.info(&format!("⚠️ {} disconnected gracefully.", addr));
                        break;
                    }

                    // An error occurred while reading input
                    Err(e) => {
                        logger.warn(&format!("❌ Error reading from {}: {}", addr, e));
                        break;
                    }
                }
            }

            // Received a shutdown signal
            _ = shutdown_rx.recv() => {
                logger.warn(&format!("🛑 {} disconnected due to shutdown.", addr));

                // Attempt to notify client of shutdown
                let mut w = sender.lock().await;
                let _ = w.write_all(b"Warning: Server is shutting down.\n").await;

                // Exit the loop
                break;
            }
        }
    }
}

/// This function spawns a background task that:
/// - Listens for a Ctrl+C signal (SIGINT)
/// - Logs the shutdown event
/// - Broadcasts a shutdown signal to all other tasks using the channel
fn spawn_shutdown_listener(shutdown_tx: broadcast::Sender<()>, logger: Logger) {
    tokio::spawn(async move {
        // Wait for Ctrl+C to be triggered
        tokio::signal::ctrl_c().await.expect("Failed to listen for ctrl_c");

        // Log the shutdown event
        logger.warn("🛑 Ctrl+C received — shutting down.");

        // Notify all subscribed tasks that it's time to shut down
        let _ = shutdown_tx.send(());
    });
}
