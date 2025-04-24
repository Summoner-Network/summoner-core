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

// Import time-related utilities for rate limiting and timeouts
use tokio::time::{self, Duration, Instant};

// Declare the configuration module for the server
pub mod config;

// Import the Logger struct from the logger module
use crate::logger::Logger;

// Import the ServerConfig struct from the server's config module
use crate::server::config::ServerConfig;

// Define a type alias for a single client's writable stream, wrapped for concurrency
pub type Client = Arc<Mutex<tokio::net::tcp::OwnedWriteHalf>>;

// Define a type alias for the shared list of all clients
pub type ClientList = Arc<Mutex<Vec<Client>>>;

/// This struct tracks client message rate for rate limiting
struct RateLimiter {
    /// Messages sent in the current window
    count: usize,
    /// Start of the current rate limiting window
    window_start: Instant,
    /// Maximum messages allowed per minute
    max_per_minute: u32,
}

impl RateLimiter {
    /// Create a new rate limiter with the specified messages per minute limit
    fn new(max_per_minute: u32) -> Self {
        Self {
            count: 0,
            window_start: Instant::now(),
            max_per_minute,
        }
    }

    /// Check if a new message would exceed the rate limit
    async fn check_limit(&mut self) -> bool {
        // Reset counter if window has elapsed (1 minute)
        let now = Instant::now();
        if now.duration_since(self.window_start) > Duration::from_secs(60) {
            self.count = 0;
            self.window_start = now;
        }

        // Increment counter and check if limit is exceeded
        self.count += 1;
        self.count <= self.max_per_minute as usize
    }
}

/// This function launches the main server loop:
/// - Binds to the provided host and port
/// - Sets up shared state and shutdown signaling
/// - Delegates to the connection handling loop
pub async fn run_server(config: ServerConfig, logger: Logger) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let addr = format!("{}:{}", config.host, config.port);

    // Bind a TCP socket to the address
    let listener = TcpListener::bind(&addr).await?;

    // Log the startup message
    logger.info(&format!("🚀 Rust server listening on {}", addr));

    // Create a shared, thread-safe vector of clients
    let clients: ClientList = Arc::new(Mutex::new(Vec::new()));

    // Set up a broadcast channel for shutdown signaling
    let (shutdown_tx, shutdown_rx) = broadcast::channel::<()>(1);

    // Set up a channel for backpressure monitoring with proper buffer size
    let (backpressure_tx, backpressure_rx) = mpsc::channel::<(SocketAddr, usize)>(config.connection_buffer_size);

    // Spawn a task to listen for Ctrl+C (SIGINT) and trigger shutdown
    spawn_shutdown_listener(shutdown_tx.clone(), logger.clone());

    // Spawn a task to monitor backpressure
    // We use let _ to intentionally ignore the returned JoinHandle
    // And we don't need to await this background task as it runs for the server's lifetime
    let _ = spawn_backpressure_monitor(backpressure_tx.clone(), backpressure_rx, logger.clone());


    // Begin accepting and handling client connections
    accept_connections(listener, clients, shutdown_tx, shutdown_rx, backpressure_tx, config, logger).await;

    // Return success
    Ok(())
}

/// Spawn a task to monitor backpressure from clients
async fn spawn_backpressure_monitor(
    _tx: mpsc::Sender<(SocketAddr, usize)>,
    mut rx: mpsc::Receiver<(SocketAddr, usize)>,
    logger: Logger,
) {
    tokio::spawn(async move {
        // Keep track of clients and their queue sizes
        let mut client_queues: std::collections::HashMap<SocketAddr, usize> = std::collections::HashMap::new();
        
        while let Some((addr, queue_size)) = rx.recv().await {
            client_queues.insert(addr, queue_size);
            
            // Log if queue size is getting large
            if queue_size > 100 {
                logger.warn(&format!("⚠️ High backpressure from client {}: {} messages queued", addr, queue_size));
                
                // Here you could implement more sophisticated backpressure handling
                // such as throttling certain clients or implementing flow control
            }
        }
    });
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
    backpressure_tx: mpsc::Sender<(SocketAddr, usize)>,
    config: ServerConfig,
    logger: Logger,
) {
    // Use atomic counter for connection tracking
    let connection_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));

    loop {
        tokio::select! {
            // Accept a new client connection
            accept_result = listener.accept() => {
                match accept_result {
                    Ok((stream, addr)) => {
                        // Update connection count
                        let current_count = connection_count.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
                        logger.info(&format!("🔌 {} connected. Active connections: {}", addr, current_count));

                        // Configure socket options
                        if let Err(e) = stream.set_nodelay(true) {
                            logger.warn(&format!("⚠️ Failed to set TCP_NODELAY for {}: {}", addr, e));
                        }

                        // Clone required context into the new task
                        let logger = logger.clone();
                        let clients = clients.clone();
                        let mut shutdown_rx = shutdown_tx.subscribe();
                        let backpressure_tx = backpressure_tx.clone();
                        let client_timeout = config.client_timeout;
                        let rate_limit = config.rate_limit;
                        let counter = connection_count.clone();

                        // Spawn a new async task for the client session
                        tokio::spawn(async move {
                            if let Err(e) = handle_connection(
                                stream, 
                                addr, 
                                clients, 
                                &mut shutdown_rx, 
                                &backpressure_tx,
                                client_timeout,
                                rate_limit,
                                &logger
                            ).await {
                                logger.warn(&format!("⚠️ Error with {}: {}", addr, e));
                            }
                            
                            // Decrement connection count when client disconnects
                            counter.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
                        });
                    },
                    Err(e) => {
                        logger.warn(&format!("⚠️ Failed to accept connection: {}", e));
                        // Brief pause to avoid CPU spinning on repeated errors
                        time::sleep(Duration::from_millis(100)).await;
                    }
                }
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
    backpressure_tx: &mpsc::Sender<(SocketAddr, usize)>,
    client_timeout: Duration,
    rate_limit: u32,
    logger: &Logger,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
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

    // Create a rate limiter for this client
    let rate_limiter = Arc::new(Mutex::new(RateLimiter::new(rate_limit)));

    // Run the message loop for this client
    handle_client_messages(
        &mut reader, 
        &clients, 
        &writer, 
        addr, 
        shutdown_rx, 
        backpressure_tx,
        client_timeout,
        rate_limiter,
        logger
    ).await?;

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

/// This function manages client activity timeouts:
/// - Tracks last active time
/// - Disconnects client after timeout period
async fn handle_client_messages(
    reader: &mut Lines<BufReader<tokio::net::tcp::OwnedReadHalf>>,
    clients: &ClientList,
    sender: &Client,
    addr: SocketAddr,
    shutdown_rx: &mut broadcast::Receiver<()>,
    backpressure_tx: &mpsc::Sender<(SocketAddr, usize)>,
    timeout: Duration,
    rate_limiter: Arc<Mutex<RateLimiter>>,
    logger: &Logger,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Create a channel for message queue size monitoring
    let (queue_tx, mut queue_rx) = mpsc::channel::<usize>(100);
    
    // Track the last activity time for timeout handling
    let mut last_active = Instant::now();
    
    // Create a periodic timer for timeout checking
    let mut timeout_interval = time::interval(Duration::from_secs(30));
    
    // Loop to handle input, shutdown, or timeout
    loop {
        tokio::select! {
            // Try reading a new line from the client
            maybe_line = reader.next_line() => {
                last_active = Instant::now(); // Update activity timestamp
                
                match maybe_line {
                    // A valid line was received
                    Ok(Some(line)) => {
                        // Check rate limit
                        let within_limit = rate_limiter.lock().await.check_limit().await;
                        if !within_limit {
                            // Notify client they're being rate limited
                            let mut w = sender.lock().await;
                            let _ = w.write_all(b"Warning: You are sending messages too quickly. Please slow down.\n").await;
                            continue; // Skip processing this message
                        }
                        
                        // Log the message with its source address
                        logger.info(&format!("📨 From {}: {}", addr, line.trim()));
                        
                        // Format the message with sender's address prefix
                        let msg = format!("[{}] {}\n", addr, line.trim());
                        
                        // Track the message queue size
                        let queue_tx = queue_tx.clone();
                        
                        // Broadcast message to all clients
                        broadcast_message(clients, sender, addr, &msg, queue_tx, logger).await;
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
            
            // Monitor queue size reports
            Some(queue_size) = queue_rx.recv() => {
                // Report backpressure if queue is getting large
                if let Err(e) = backpressure_tx.send((addr, queue_size)).await {
                    logger.warn(&format!("Failed to report backpressure: {}", e));
                }
            }
            
            // Check for client timeout
            _ = timeout_interval.tick() => {
                if last_active.elapsed() > timeout {
                    // Client has been inactive for too long
                    logger.info(&format!("⏰ Client {} timed out after {:?} of inactivity", addr, timeout));
                    
                    // Notify client of timeout
                    let mut w = sender.lock().await;
                    let _ = w.write_all(b"Warning: You have been disconnected due to inactivity.\n").await;
                    
                    // Exit the loop to terminate the connection
                    break;
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
    
    Ok(())
}

/// Broadcast a message to all connected clients except the sender
async fn broadcast_message(
    clients: &ClientList,
    sender: &Client,
    addr: SocketAddr,
    msg: &str,
    queue_tx: mpsc::Sender<usize>,
    logger: &Logger
) {
    // Lock the client list to broadcast to all others
    let others = clients.lock().await;
    
    // Count pending messages for backpressure monitoring
    let queue_size = others.len();
    
    // Try to send queue size through the channel
    if queue_tx.try_send(queue_size).is_err() {
        logger.warn(&format!("⚠️ Backpressure monitoring channel full for client {}", addr));
    }
    
    // Send to each client
    for client in others.iter() {
        // Don't send the message back to the sender
        if Arc::ptr_eq(sender, client) {
            continue;
        }
        
        // Lock each client's writer and send the message
        let mut w = client.lock().await;
        if let Err(e) = w.write_all(msg.as_bytes()).await {
            logger.warn(&format!("❌ Failed to send to client: {}", e));
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
        if let Err(e) = tokio::signal::ctrl_c().await {
            logger.error(&format!("Failed to listen for ctrl_c: {}", e));
            return;
        }
        
        // Log the shutdown event
        logger.warn("🛑 Ctrl+C received — shutting down.");
        
        // Notify all subscribed tasks that it's time to shut down
        let _ = shutdown_tx.send(());
    });
}