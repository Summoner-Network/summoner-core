/// === IMPORTS ===

// Standard library type for holding an IP address and port together.
use std::net::SocketAddr;

// Arc is an atomic reference counter for shared ownership across threads/tasks.
// Mutex provides safe, asynchronous locking for mutable data.
// We'll use Arc<Mutex<...>> to share a connection's writer handle safely.
use std::sync::Arc;

// Tokio's non-blocking TCP listener and stream for incoming/outgoing connections.
use tokio::net::{TcpListener, TcpStream};

// Utilities for buffered, line-by-line asynchronous I/O on TCP streams.
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines};

// Asynchronous synchronization and messaging:
// - Mutex: async-aware lock for exclusive data access.
// - RwLock: async-aware lock allowing many readers or one writer.
// - broadcast: one-to-many channel (used here for shutdown signals).
// - mpsc: multi-producer, single-consumer channel (used for backpressure and control).
use tokio::sync::{Mutex, RwLock, broadcast, mpsc};

// Time tools for delays, timeouts, and measuring elapsed time.
use tokio::time::{self, Duration, Instant};

// Macro for building JSON payloads when broadcasting client messages.
use serde_json::{json, Value as JsonValue};

// Clone-on-write string type: avoids extra allocations when we don't modify the string.
use std::borrow::Cow;

// Reference-counted byte buffer: enables zero-copy sharing of message data.
use bytes::Bytes;


/// === MODULES ===

// Private modules handling specific server features.
mod backpressure;   // queue monitoring and control commands
mod ratelimiter;    // per-client rate limiting
mod quarantine;     // temporary client bans


// Import the parsed ServerConfig struct that holds all user settings.
use crate::config::ServerConfig;

// Logger utility to record informational and error messages.
use crate::logger::{Logger, prune_content_value};

// Backpressure commands and helper to spawn the backpressure monitoring task.
use crate::server::backpressure::{BackpressureCommand, ClientCommand, spawn_backpressure_monitor};

// Token-bucket-style rate limiter for each client's message flow.
use crate::server::ratelimiter::RateLimiter;

// Quarantine list for tracking and expiring banned clients over time.
use crate::server::quarantine::QuarantineList;


/// === TYPES ===

// Represents one connected client. Cloning this struct is cheap (Arc + channel).
#[derive(Clone)]
pub struct Client {
    // The client's socket address (used for logging and identification).
    pub addr: SocketAddr,

    // The write-half of the TCP stream, wrapped in Arc<Mutex<...>> so multiple tasks
    // can safely send data to this client without data races.
    pub writer: Arc<Mutex<tokio::net::tcp::OwnedWriteHalf>>,

    // Channel to send per-client control commands (throttle, flow-control) to that client's task.
    pub control_tx: mpsc::Sender<ClientCommand>,
}

// A shared, asynchronous list of all connected clients.
// - Arc allows multiple owners across tasks.
// - RwLock lets many readers (e.g. broadcasts) but only one writer (add/remove) at a time.
pub type ClientList = Arc<RwLock<Vec<Client>>>;

/// **Represents a connection to another server instance in the mesh.**
#[derive(Clone)]
pub struct Peer {
    /// The peer's configured address string (e.g., "10.0.0.2:8888")
    pub addr: String,
    /// The write-half of the TCP stream for sending messages to this peer.
    pub writer: Arc<Mutex<tokio::net::tcp::OwnedWriteHalf>>,
}

/// **A shared, asynchronous list of all connected peers.**
pub type PeerList = Arc<RwLock<Vec<Peer>>>;


/// === RUN_SERVER ===

/// This function launches the main server loop:
/// - Binds to the provided host and port
/// - Spawns tasks to connect to peers
/// - Sets up shared state and channels
/// - Spawns background tasks for shutdown, backpressure, quarantine cleanup
/// - Starts listening for incoming client and peer connections
pub async fn run_server(
    config: ServerConfig,
    logger: Logger,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Build the "host:port" string so the OS knows where to listen
    let addr = format!("{}:{}", config.host, config.port);

    // Open a TCP listener on that address; the `?` returns early on error
    let listener = TcpListener::bind(&addr).await?;

    // Tell our logger that the server is up and running
    logger.info(&format!("🚀 Rust server listening on {}", addr));

    // Prepare shared lists for connected clients and peers
    // Wrapped in Arc+RwLock so many tasks can read, but only one can write at a time
    let clients: ClientList = Arc::new(RwLock::new(Vec::new()));
    let peers: PeerList = Arc::new(RwLock::new(Vec::new()));

    // Create a broadcast channel for shutdown signals
    // `shutdown_tx` sends the signal; `shutdown_rx` receives it in each task
    let (shutdown_tx, shutdown_rx) = broadcast::channel::<()>(1);

    // Channel where each client reports its current message queue length
    // The capacity is set so backpressure logic can buffer incoming reports
    let (backpressure_tx, backpressure_rx) =
        mpsc::channel::<(SocketAddr, usize)>(config.connection_buffer_size);

    // Channel used by the backpressure monitor to send commands (Throttle, Disconnect, etc.)
    let (command_tx, command_rx) =
        mpsc::channel::<BackpressureCommand>(config.command_buffer_size);

    // Track which clients are quarantined (banned temporarily)
    // Wrapped in Arc+Mutex for safe, exclusive access when marking or cleaning entries
    let quarantine_list = Arc::new(Mutex::new(
        QuarantineList::new(Duration::from_secs(config.quarantine_cooldown_secs))
    ));

    // Spawn a task to proactively connect to all configured peers.
    if let Some(peer_addresses) = &config.peer_addresses {
        for peer_addr in peer_addresses {
            // Don't connect to ourself.
            if Some(peer_addr) == config.local_address.as_ref() {
                continue;
            }
            spawn_peer_connector(
                peer_addr.clone(),
                peers.clone(),
                clients.clone(),
                shutdown_tx.subscribe(),
                logger.clone(),
            );
        }
    }

    // Spawn a background task that every N seconds removes old entries from quarantine
    // We keep the JoinHandle so we can cancel it cleanly on shutdown
    let cleanup_handle = QuarantineList::start_background_cleanup(
        quarantine_list.clone(),
        Duration::from_secs(config.quarantine_cleanup_interval_secs),
        logger.clone(),
    );

    // Spawn a task that waits for Ctrl+C, then broadcasts the shutdown signal
    // We capture its handle to abort it later
    let shutdown_handle = spawn_shutdown_listener(shutdown_tx.clone(), logger.clone());

    // Spawn the backpressure monitor: it reads queue sizes and sends control commands
    // We also keep its handle so we can abort on shutdown
    let backpressure_handle = spawn_backpressure_monitor(
        backpressure_rx,
        command_tx,
        logger.clone(),
        config.backpressure_policy.clone(),
    );

    // Hand off to the function that loops accepting new connections and handling commands
    // We await it so we stay here until all clients have disconnected and shutdown is complete
    accept_connections(
        listener,
        clients,
        peers,
        shutdown_tx,
        shutdown_rx,
        backpressure_tx,
        command_rx,
        quarantine_list,
        config,
        logger,
    )
    .await;

    // Once `accept_connections` returns, every client task has finished.
    // Now abort our three background tasks to free resources immediately.
    cleanup_handle.abort();
    shutdown_handle.abort();
    backpressure_handle.abort();

    // All done, return success to the caller
    Ok(())
}

/// This function spawns a background task that:
/// - Listens for a Ctrl+C signal (SIGINT)
/// - Logs the shutdown event
/// - Broadcasts a shutdown signal to all other tasks using the channel
fn spawn_shutdown_listener(
    shutdown_tx: broadcast::Sender<()>,
    logger: Logger,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        if let Err(e) = tokio::signal::ctrl_c().await {
            logger.error(&format!("Failed to listen for ctrl_c: {}", e));
            return;
        }
        logger.warn("🛑 Ctrl+C received — shutting down.");
        let _ = shutdown_tx.send(());
    })
}

/// === CONNECTIONS ===

/// Listens for new clients/peers, spawns per-connection tasks, and handles shutdown/backpressure
async fn accept_connections(
    listener: TcpListener,                      // TCP socket we bound in run_server
    clients: ClientList,                        // Shared list of connected clients
    peers: PeerList,                            // Shared list of connected peers
    shutdown_tx: broadcast::Sender<()>,         // Sender to broadcast shutdown to all tasks
    mut shutdown_rx: broadcast::Receiver<()>,   // Receiver to hear the shutdown signal
    backpressure_tx: mpsc::Sender<(SocketAddr, usize)>, // Where client tasks report queue sizes
    mut command_rx: mpsc::Receiver<BackpressureCommand>, // Commands from the backpressure monitor
    quarantine_list: Arc<Mutex<QuarantineList>>, // Tracks temporarily banned clients
    config: ServerConfig,                       // All of our runtime settings
    logger: Logger,                             // Logging handle
) {
    let connection_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));

    loop {
        tokio::select! {
            // 1) New connection arrives
            accept_result = listener.accept() => {
                match accept_result {
                    Ok((stream, addr)) => {
                        // Dispatch the new connection to the correct handler
                        handle_new_connection(
                            stream,
                            addr,
                            &clients,
                            &peers,
                            &shutdown_tx,
                            &backpressure_tx,
                            &quarantine_list,
                            connection_count.clone(),
                            &config,
                            &logger,
                        ).await;
                    }
                    Err(e) => {
                        logger.warn(&format!("⚠️ Failed to accept connection: {}", e));
                        time::sleep(Duration::from_millis(config.accept_error_backoff_ms)).await;
                    }
                }
            }

            // 2) A backpressure monitor command arrives (throttle, flow-control, disconnect)
            Some(cmd) = command_rx.recv() => {
                handle_backpressure_command(cmd, &clients, &quarantine_list, &logger).await;
            }

            // 3) A global shutdown signal arrived (e.g. Ctrl+C)
            _ = shutdown_rx.recv() => {
                logger.info("🧹 Server received shutdown signal.");
                break;
            }
        }
    }
}

/// Apply a backpressure command to the relevant client(s)
async fn handle_backpressure_command(
    cmd: BackpressureCommand,
    clients: &ClientList,
    quarantine_list: &Arc<Mutex<QuarantineList>>,
    logger: &Logger,
) {
    match cmd {
        BackpressureCommand::Disconnect(addr) => {
            // Remove the client in one shot
            {
                let mut clients = clients.write().await;
                clients.retain(|c| c.addr != addr);
            }
            // Quarantine them
            {
                let mut q = quarantine_list.lock().await;
                q.insert(addr);
            }
            logger.info(&format!("🛑 Client {} added to quarantine", addr));
        }

        BackpressureCommand::Throttle(addr) | BackpressureCommand::FlowControl(addr) => {
            // Decide which control command to send
            let control_cmd = match cmd {
                BackpressureCommand::Throttle(_)    => ClientCommand::Throttle,
                BackpressureCommand::FlowControl(_) => ClientCommand::FlowControl,
                _ => unreachable!(),
            };

            let maybe_tx = {
                let clients = clients.read().await;
                clients
                    .iter()
                    .find(|c| c.addr == addr)
                    .map(|c| c.control_tx.clone())
            };

            if let Some(tx) = maybe_tx {
                if let Err(e) = tx.try_send(control_cmd) {
                    logger.warn(&format!(
                        "Failed to send {:?} to {}: {}",
                        control_cmd, addr, e
                    ));
                }
                let emoji = match control_cmd {
                    ClientCommand::Throttle    => "⏳",
                    ClientCommand::FlowControl => "⏸️",
                };
                logger.info(&format!("{} {:?} requested for {}", emoji, control_cmd, addr));
            } else {
                logger.warn(&format!(
                    "Backpressure command for unknown client {}: {:?}",
                    addr, control_cmd
                ));
            }
        }
    }
}

/// **Determines if a connection is a client or a peer, then dispatches it.**
async fn handle_new_connection(
    stream: TcpStream,
    addr: SocketAddr,
    clients: &ClientList,
    peers: &PeerList,
    shutdown_tx: &broadcast::Sender<()>,
    backpressure_tx: &mpsc::Sender<(SocketAddr, usize)>,
    quarantine_list: &Arc<Mutex<QuarantineList>>,
    connection_count: Arc<std::sync::atomic::AtomicUsize>,
    config: &ServerConfig,
    logger: &Logger,
) {
    // Check if the incoming connection's IP matches a configured peer IP.
    let is_peer = config
        .peer_addresses
        .as_deref()
        .unwrap_or(&[])
        .iter()
        .any(|peer_addr_str| peer_addr_str.starts_with(&addr.ip().to_string()));

    if is_peer {
        // This is an incoming connection from another peer server.
        handle_new_peer_connection(
            stream,
            addr,
            peers,
            clients,
            shutdown_tx.subscribe(),
            logger.clone(),
        )
        .await;
    } else {
        // This is a regular client connection.
        handle_new_client_connection(
            stream,
            addr,
            clients,
            peers,
            shutdown_tx,
            backpressure_tx,
            quarantine_list,
            connection_count,
            config,
            logger,
        )
        .await;
    }
}

/// Validates a new client TCP connection and then spawns its session task
async fn handle_new_client_connection(
    stream: TcpStream,
    addr: SocketAddr,
    clients: &ClientList,
    peers: &PeerList,
    shutdown_tx: &broadcast::Sender<()>,
    backpressure_tx: &mpsc::Sender<(SocketAddr, usize)>,
    quarantine_list: &Arc<Mutex<QuarantineList>>,
    connection_count: Arc<std::sync::atomic::AtomicUsize>,
    config: &ServerConfig,
    logger: &Logger,
) {
    // 1) Reject any client still in quarantine
    {
        let q = quarantine_list.lock().await;
        if q.is_quarantined(&addr) {
            logger.info(&format!("🔒 Client {} is quarantined — ignoring connection", addr));
            return;
        }
    }

    // 2) Prevent duplicate connections from the same address
    {
        let list = clients.read().await;
        if list.iter().any(|c| c.addr == addr) {
            logger.warn(&format!("Duplicate connection from {} rejected", addr));
            return;
        }
    }

    // 3) Disable Nagle's algorithm to reduce latency
    if let Err(e) = stream.set_nodelay(true) {
        logger.warn(&format!("⚠️  Failed to set TCP_NODELAY for {}: {}", addr, e));
    }

    // 4) Split the stream and create a control channel
    let (reader_half, writer_half) = stream.into_split();
    let (control_tx, control_rx) = mpsc::channel(config.control_channel_capacity);

    // 5) Build our Client struct
    let client = Client {
        addr,
        writer: Arc::new(Mutex::new(writer_half)),
        control_tx,
    };

    // 6) Register the client in the global list
    {
        let mut list = clients.write().await;
        list.push(client.clone());
    }

    // 7) Increment and log the connection count
    let current = connection_count.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
    logger.info(&format!("🔌 {} connected. Active connections: {}", addr, current));

    // 8) Clone everything needed for the async task
    let clients_for_task    = clients.clone();
    let peers_for_task      = peers.clone();
    let logger              = logger.clone();
    let config              = config.clone();
    let mut shutdown_rx     = shutdown_tx.subscribe();
    let backpressure_tx     = backpressure_tx.clone();
    let client_timeout      = config.client_timeout;
    let rate_limit          = config.rate_limit;

    // 9) Spawn the per-client session loop
    tokio::spawn(async move {
        if let Err(e) = handle_client_connection(
            reader_half,
            client.clone(),
            clients_for_task,
            peers_for_task,
            &mut shutdown_rx,
            &backpressure_tx,
            client_timeout,
            rate_limit,
            control_rx,
            &config,
            &logger,
        )
        .await
        {
            logger.warn(&format!("⚠️ Error with {}: {}", addr, e));
        }

        let remaining = connection_count.fetch_sub(1, std::sync::atomic::Ordering::SeqCst) - 1;
        logger.info(&format!("🔌 {} disconnected. Active connections: {}", addr, remaining));
    });

}

/// Manages a single client's session, reading lines and broadcasting them.
async fn handle_client_connection(
    reader_half: tokio::net::tcp::OwnedReadHalf,
    client: Client,
    clients: ClientList,
    peers: PeerList, // <-- New: needed for broadcasting
    shutdown_rx: &mut broadcast::Receiver<()>,
    backpressure_tx: &mpsc::Sender<(SocketAddr, usize)>,
    client_timeout: Option<Duration>,
    rate_limit: u32,
    control_rx: mpsc::Receiver<ClientCommand>,
    config: &ServerConfig,
    logger: &Logger,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let mut reader = BufReader::new(reader_half).lines();
    let rate_limiter = Arc::new(Mutex::new(RateLimiter::new(rate_limit)));

    // The message loop now gets the peer list to broadcast to.
    if let Err(e) = handle_client_messages(
        &mut reader,
        &client,
        &clients,
        &peers, // <-- New
        shutdown_rx,
        backpressure_tx,
        client_timeout,
        rate_limiter,
        config,
        logger,
        control_rx,
    )
    .await
    {
        logger.warn(&format!("⚠️ Error in client {} session: {}", client.addr, e));
    }

    // Cleanup: remove this client from the active list
    {
        let mut list = clients.write().await;
        list.retain(|c| c.addr != client.addr);
    }
    logger.info(&format!("🧼 Client {} removed.", client.addr));
    Ok(())
}


/// === MESSAGES ===

/// Manages a single client session until disconnect or shutdown.
async fn handle_client_messages(
    reader: &mut Lines<BufReader<tokio::net::tcp::OwnedReadHalf>>,
    sender: &Client,
    clients: &ClientList,
    peers: &PeerList,
    shutdown_rx: &mut broadcast::Receiver<()>,
    backpressure_tx: &mpsc::Sender<(SocketAddr, usize)>,
    timeout: Option<Duration>,
    rate_limiter: Arc<Mutex<RateLimiter>>,
    config: &ServerConfig,
    logger: &Logger,
    mut control_rx: mpsc::Receiver<ClientCommand>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let (queue_tx, mut queue_rx) = mpsc::channel::<usize>(config.queue_monitor_capacity);
    let mut last_active = Instant::now();
    let mut timeout_interval = time::interval(Duration::from_secs(
        config.timeout_check_interval_secs,
    ));

    loop {
        tokio::select! {
            // 1) Incoming line from client
            maybe_line = reader.next_line() => {
                last_active = Instant::now();
                match maybe_line {
                    Ok(Some(line)) => {
                        // Process the line and broadcast to clients AND peers
                        process_client_line(
                            line,
                            sender,
                            clients,
                            peers, // <-- New
                            &rate_limiter,
                            queue_tx.clone(),
                            config,
                            logger,
                        ).await;
                    }
                    Ok(None) => {
                        logger.info(&format!("⚠️ {} disconnected gracefully.", sender.addr));
                        break;
                    }
                    Err(e) => {
                        logger.warn(&format!("❌ Error reading from {}: {}", sender.addr, e));
                        break;
                    }
                }
            }

            // 2) Throttle or flow-control command arrived
            Some(cmd) = control_rx.recv() => {
                apply_client_command(cmd, sender, config, logger).await;
            }

            // 3) Report our queue size for backpressure
            Some(queue_size) = queue_rx.recv() => {
                let addr = sender.addr;
                let tx = backpressure_tx.clone();
                let log = logger.clone();
                tokio::spawn(async move {
                    if let Err(e) = tx.try_send((addr, queue_size)) {
                        log.warn(&format!("⚠️ Backpressure channel full for {}: {}", addr, e));
                    }
                });
            }

            // 4) Check for inactivity timeout
            _ = timeout_interval.tick() => {
                if let Some(timeout) = timeout {
                    if last_active.elapsed() > timeout {
                        logger.info(&format!("⏰ Client {} timed out", sender.addr));
                        let mut w = sender.writer.lock().await;
                        let _ = w.write_all(b"Warning: Disconnected due to inactivity.\n").await;
                        break;
                    }
                }
            }

            // 5) Global shutdown signal
            _ = shutdown_rx.recv() => {
                logger.warn(&format!("🛑 {} disconnected due to shutdown.", sender.addr));
                let mut w = sender.writer.lock().await;
                let _ = w.write_all(b"Warning: Server is shutting down.\n").await;
                break;
            }
        }
    }
    Ok(())
}

/// Responds to a throttle or flow-control command by pausing the client's processing
async fn apply_client_command(
    cmd: ClientCommand,
    sender: &Client,
    config: &ServerConfig,
    logger: &Logger,
) {
    match cmd {
        ClientCommand::Throttle => {
            logger.info(&format!("⏳ Throttling {}", sender.addr));
            tokio::time::sleep(Duration::from_millis(config.throttle_delay_ms)).await;
        }
        ClientCommand::FlowControl => {
            logger.info(&format!("⏸️ Pausing {} for flow control", sender.addr));
            tokio::time::sleep(Duration::from_millis(config.flow_control_delay_ms)).await;
        }
    }
}

/// Processes a single client message and broadcasts to clients AND peers.
async fn process_client_line(
    line: String,
    sender: &Client,
    clients: &ClientList,
    peers: &PeerList, // <-- New
    rate_limiter: &Arc<Mutex<RateLimiter>>,
    queue_tx: mpsc::Sender<usize>,
    config: &ServerConfig,
    logger: &Logger,
) {
    if !rate_limiter.lock().await.check_limit() {
        let mut w = sender.writer.lock().await;
        let _ = w.write_all(b"Warning: Sending messages too quickly.\n").await;
        return;
    }

    let content = remove_last_newline(&line);
    let envelope = json!({
        "remote_addr": sender.addr.to_string(),
        "content": content,
    });
    let envelope_text = envelope.to_string();

    let json_content: JsonValue = serde_json::from_str(&content).unwrap_or(JsonValue::String(content.to_string()));
    let pruned_content = if config.logger.log_keys.is_some() {
        prune_content_value(json_content, &config.logger.log_keys)
    } else {
        json_content
    };

    let log_body = if config.logger.enable_json_log {
        json!({"remote_addr": sender.addr, "content": pruned_content}).to_string()
    } else {
        format!("📨 From {}: {}", sender.addr, pruned_content)
    };
    logger.info(&log_body);

    // **BROADCAST TO BOTH PEERS AND CLIENTS**
    let msg_bytes = Arc::new(Bytes::from(ensure_trailing_newline(&envelope_text).into_owned()));

    // Report queue size based on number of clients + peers
    let client_count = clients.read().await.len().saturating_sub(1);
    let peer_count = peers.read().await.len();
    let _ = queue_tx.try_send(client_count + peer_count);

    // Send to other clients
    broadcast_to_clients(clients, msg_bytes.clone(), logger, Some(sender.addr)).await;

    // Send to all peers
    broadcast_to_peers(peers, msg_bytes, logger).await;
}

/// Removes the last `\n` from the string if present.
fn remove_last_newline(s: &str) -> &str {
    s.strip_suffix('\n').unwrap_or(s)
}

/// Ensures the string ends with exactly one newline (`\n`).
fn ensure_trailing_newline(s: &str) -> Cow<'_, str> {
    if s.ends_with('\n') {
        Cow::Borrowed(s)
    } else {
        Cow::Owned(format!("{s}\n"))
    }
}

// =========================================================================
// == PEER-SPECIFIC LOGIC ==================================================
// =========================================================================

/// Spawns a task that perpetually tries to connect to a single peer.
fn spawn_peer_connector(
    peer_addr: String,
    peers: PeerList,
    clients: ClientList,
    mut shutdown_rx: broadcast::Receiver<()>,
    logger: Logger,
) {
    tokio::spawn(async move {
        loop {
            tokio::select! {
                _ = shutdown_rx.recv() => {
                    logger.info(&format!("Peer connector for {} shutting down.", peer_addr));
                    break;
                },
                connect_result = TcpStream::connect(&peer_addr) => {
                    match connect_result {
                        Ok(stream) => {
                            logger.info(&format!("🤝 Connected TO peer: {}", peer_addr));
                            let remote_addr = stream.peer_addr().ok();
                            if let Err(e) = stream.set_nodelay(true) {
                                logger.warn(&format!("Failed to set TCP_NODELAY for peer {}: {}", peer_addr, e));
                            }
                            let (reader, writer) = stream.into_split();

                            let peer = Peer {
                                addr: peer_addr.clone(),
                                writer: Arc::new(Mutex::new(writer)),
                            };
                            peers.write().await.push(peer);

                            // This task now handles messages *from* the peer we connected to.
                            handle_peer_messages(
                                reader,
                                remote_addr,
                                &peer_addr,
                                &clients,
                                &mut shutdown_rx,
                                &logger,
                            ).await;

                            // If the handler returns, the peer disconnected. Remove it.
                            peers.write().await.retain(|p| p.addr != peer_addr);
                            logger.warn(&format!("Peer {} disconnected. Will attempt to reconnect.", peer_addr));
                        }
                        Err(_) => {
                            // Suppress noisy logs: logger.warn(&format!("Failed to connect to peer {}: {}. Retrying...", peer_addr, e));
                        }
                    }
                }
            }
            // Wait before retrying connection
            time::sleep(Duration::from_secs(5)).await;
        }
    });
}

/// Registers an incoming connection from a peer and spawns its message loop.
async fn handle_new_peer_connection(
    stream: TcpStream,
    addr: SocketAddr,
    peers: &PeerList,
    clients: &ClientList,
    mut shutdown_rx: broadcast::Receiver<()>,
    logger: Logger,
) {
    let peer_addr_str = addr.to_string();
    logger.info(&format!("🤝 Accepted connection FROM peer: {}", peer_addr_str));

    if let Err(e) = stream.set_nodelay(true) {
        logger.warn(&format!("Failed to set TCP_NODELAY for peer {}: {}", addr, e));
    }
    let (reader, writer) = stream.into_split();
    
    let peer = Peer {
        addr: peer_addr_str.clone(),
        writer: Arc::new(Mutex::new(writer)),
    };
    peers.write().await.push(peer);

    // This task handles messages *from* the peer that connected to us.
    handle_peer_messages(
        reader,
        Some(addr),
        &peer_addr_str,
        clients,
        &mut shutdown_rx,
        &logger,
    ).await;

    // If the handler returns, the peer disconnected. Remove it.
    peers.write().await.retain(|p| p.addr != peer_addr_str);
    logger.warn(&format!("Peer {} disconnected.", peer_addr_str));
}

/// Reads messages from a single peer and broadcasts them ONLY to local clients.
async fn handle_peer_messages(
    reader: tokio::net::tcp::OwnedReadHalf,
    remote_addr: Option<SocketAddr>,
    peer_addr_str: &str,
    clients: &ClientList,
    shutdown_rx: &mut broadcast::Receiver<()>,
    logger: &Logger,
) {
    let mut reader = BufReader::new(reader).lines();
    loop {
        tokio::select! {
            maybe_line = reader.next_line() => {
                match maybe_line {
                    Ok(Some(line)) => {
                        // The line from a peer is already a full JSON envelope.
                        // We do NOT re-wrap or log it. We just forward it.
                        let msg_bytes = Arc::new(Bytes::from(ensure_trailing_newline(&line).into_owned()));
                        broadcast_to_clients(clients, msg_bytes, logger, None).await;
                    }
                    Ok(None) | Err(_) => {
                        // Peer disconnected.
                        break;
                    }
                }
            }
            _ = shutdown_rx.recv() => {
                break;
            }
        }
    }
}

/// Sends a message to all connected clients, optionally excluding one.
async fn broadcast_to_clients(
    clients: &ClientList,
    msg_bytes: Arc<Bytes>,
    logger: &Logger,
    exclude_addr: Option<SocketAddr>,
) {
    let snapshot: Vec<_> = clients
        .read()
        .await
        .iter()
        .filter(|c| exclude_addr.map_or(true, |addr| c.addr != addr))
        .cloned()
        .collect();

    for client in snapshot {
        let writer = client.writer.clone();
        let buf = msg_bytes.clone();
        let addr = client.addr;
        let log = logger.clone();

        tokio::spawn(async move {
            let mut w = writer.lock().await;
            if let Err(e) = w.write_all(&buf).await {
                log.warn(&format!("❌ Failed to send to client {}: {}", addr, e));
            }
        });
    }
}

/// Sends a message to all connected peers.
async fn broadcast_to_peers(peers: &PeerList, msg_bytes: Arc<Bytes>, logger: &Logger) {
    let snapshot: Vec<_> = peers.read().await.iter().cloned().collect();

    for peer in snapshot {
        let writer = peer.writer.clone();
        let buf = msg_bytes.clone();
        let addr = peer.addr.clone();
        let log = logger.clone();

        tokio::spawn(async move {
            let mut w = writer.lock().await;
            if let Err(e) = w.write_all(&buf).await {
                log.warn(&format!("❌ Failed to send to peer {}: {}", addr, e));
            }
        });
    }
}