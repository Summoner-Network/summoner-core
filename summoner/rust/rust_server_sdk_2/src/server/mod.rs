/// === IMPORTS ===

// Standard library type for holding an IP address and port together.
use std::net::SocketAddr;

// Arc is an atomic reference counter for shared ownership across threads/tasks.
// Mutex provides safe, asynchronous locking for mutable data.
// We'll use Arc<Mutex<...>> to share a client's writer handle safely.
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
use serde_json::json;

// Clone-on-write string type: avoids extra allocations when we don't modify the string.
use std::borrow::Cow;

// Reference-counted byte buffer: enables zero-copy sharing of message data.
use bytes::Bytes;


/// === MODULES ===

// Public submodule for parsing and validating server configuration from Python.
pub mod config;

// Private modules handling specific server features.
mod backpressure;   // queue monitoring and control commands
mod ratelimiter;    // per-client rate limiting
mod quarantine;     // temporary client bans

// Logger utility to record informational and error messages.
use crate::logger::Logger;

// Import the parsed ServerConfig struct that holds all user settings.
use crate::server::config::ServerConfig;

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


/// === RUN_SERVER ===

/// This function launches the main server loop:
/// - Binds to the provided host and port
/// - Sets up shared state and channels
/// - Spawns background tasks for shutdown, backpressure, quarantine cleanup
/// - Starts listening for incoming client connections
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

    // Prepare an empty, shared list of connected clients
    // Wrapped in Arc+RwLock so many tasks can read, but only one can write at a time
    let clients: ClientList = Arc::new(RwLock::new(Vec::new()));

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

    // Hand off to the function that loops accepting new clients and handling commands
    // We await it so we stay here until all clients have disconnected and shutdown is complete
    accept_connections(
        listener,
        clients,
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


/// === CONNECTIONS ===

/// Listens for new clients, spawns per-client tasks, and handles shutdown/backpressure
async fn accept_connections(
    listener: TcpListener,                      // TCP socket we bound in run_server
    clients: ClientList,                        // Shared list of connected clients
    shutdown_tx: broadcast::Sender<()>,         // Sender to broadcast shutdown to clients
    mut shutdown_rx: broadcast::Receiver<()>,   // Receiver to hear the shutdown signal
    backpressure_tx: mpsc::Sender<(SocketAddr, usize)>, // Where client tasks report queue sizes
    mut command_rx: mpsc::Receiver<BackpressureCommand>, // Commands from the backpressure monitor
    quarantine_list: Arc<Mutex<QuarantineList>>, // Tracks temporarily banned clients
    config: ServerConfig,                       // All of our runtime settings
    logger: Logger,                             // Logging handle
) {
    // A simple counter to track how many clients are currently connected
    let connection_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));

    // We loop forever (or until a shutdown signal breaks us out)
    loop {
        tokio::select! {
            // 1) New client arrives
            accept_result = listener.accept() => {
                match accept_result {
                    // If accept succeeds, we get a TCP stream and the peer address
                    Ok((stream, addr)) => {
                        // Hand off the new connection to its own async function
                        handle_new_connection(
                            stream,
                            addr,
                            &clients,
                            &shutdown_tx,
                            &backpressure_tx,
                            &quarantine_list,
                            connection_count.clone(), // share the counter
                            &config,
                            &logger,
                        ).await;
                    }
                    // If accept failed (e.g. too many open files), warn and pause briefly
                    Err(e) => {
                        logger.warn(&format!("⚠️ Failed to accept connection: {}", e));
                        time::sleep(Duration::from_millis(config.accept_error_backoff_ms)).await;
                    }
                }
            }

            // 2) A backpressure monitor command arrives (throttle, flow-control, disconnect)
            Some(cmd) = command_rx.recv() => {
                // Apply it immediately to the matching client(s)
                handle_backpressure_command(cmd, &clients, &quarantine_list, &logger).await;
            }

            // 3) A global shutdown signal arrived (e.g. Ctrl+C)
            _ = shutdown_rx.recv() => {
                // Log that we're beginning graceful shutdown
                logger.info("🧹 Server received shutdown signal.");
                // Break out of the loop so run_server can clean up
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

            // Under a short read lock, grab a clone of the sender if the client still exists
            let maybe_tx = {
                let clients = clients.read().await;
                clients
                    .iter()
                    .find(|c| c.addr == addr)
                    .map(|c| c.control_tx.clone())
            };

            // Now drop the lock and actually send the command
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

/// Validates a new TCP connection and then spawns its session task
async fn handle_new_connection(
    stream: TcpStream,                           // The raw TCP connection
    addr: SocketAddr,                            // Remote client address (IP:port)
    clients: &ClientList,                        // Shared list of all active clients
    shutdown_tx: &broadcast::Sender<()>,         // Sender to broadcast shutdown to each client
    backpressure_tx: &mpsc::Sender<(SocketAddr, usize)>, // Where clients report their queue sizes
    quarantine_list: &Arc<Mutex<QuarantineList>>, // Tracks temporarily banned clients
    connection_count: Arc<std::sync::atomic::AtomicUsize>, // Global counter of active clients
    config: &ServerConfig,                       // All runtime settings
    logger: &Logger,                             // Logging handle
) {
    // 1) Reject any client still in quarantine
    {
        let q = quarantine_list.lock().await;
        if q.is_quarantined(&addr) {
            logger.info(&format!("🔒 Client {} is quarantined — ignoring connection", addr));
            return;  // Drop the stream by returning early
        }
    }

    // 2) Prevent duplicate connections from the same address
    {
        let list = clients.read().await;  // Read-lock while checking
        if list.iter().any(|c| c.addr == addr) {
            logger.warn(&format!("Duplicate connection from {} rejected", addr));
            return;
        }
    }

    // 3) Disable Nagle's algorithm to reduce latency (small packets go out immediately)
    if let Err(e) = stream.set_nodelay(true) {
        logger.warn(&format!("⚠️  Failed to set TCP_NODELAY for {}: {}", addr, e));
    }

    // 4) Split the stream into read/write halves for independent tasks
    let (reader_half, writer_half) = stream.into_split();

    // 5) Create a dedicated control channel for backpressure commands to this client
    let (control_tx, control_rx) = mpsc::channel(config.control_channel_capacity);

    // 6) Build our Client struct, wrapping the writer in Arc<Mutex> for safe sharing
    let client = Client {
        addr,
        writer: Arc::new(Mutex::new(writer_half)),
        control_tx,
    };

    // 7) Register the client in the global list under a write-lock
    {
        let mut list = clients.write().await;
        list.push(client.clone());
    }

    // 8) Increment and log the connection count
    let current = connection_count.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
    logger.info(&format!("🔌 {} connected. Active connections: {}", addr, current));

    // 9) Clone everything needed into the async task
    let clients_for_task    = clients.clone();
    let logger              = logger.clone();
    let config              = config.clone();
    let mut shutdown_rx     = shutdown_tx.subscribe();
    let backpressure_tx     = backpressure_tx.clone();
    let client_timeout      = config.client_timeout;
    let rate_limit          = config.rate_limit;

    // 10) Spawn the per-client session loop
    // Spawn the per-client session loop and log teardown cleanly
    tokio::spawn(async move {
        // Run the client message loop
        if let Err(e) = handle_connection(
            reader_half,
            client.clone(),
            clients_for_task,
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

        // Decrement the counter and log how many remain
        let remaining = connection_count.fetch_sub(1, std::sync::atomic::Ordering::SeqCst) - 1;
        logger.info(&format!("🔌 {} disconnected. Active connections: {}", addr, remaining));
    });

}

/// Manages a single client's session:
/// - Reads incoming lines and applies rate limiting
/// - Reports backpressure without blocking
/// - Enforces inactivity timeouts and graceful shutdown
/// - On exit, removes the client from the shared list and logs the remaining count
async fn handle_connection(
    reader_half: tokio::net::tcp::OwnedReadHalf,
    client: Client,
    clients: ClientList,
    shutdown_rx: &mut broadcast::Receiver<()>,
    backpressure_tx: &mpsc::Sender<(SocketAddr, usize)>,
    client_timeout: Option<Duration>,
    rate_limit: u32,
    control_rx: mpsc::Receiver<ClientCommand>,
    config: &ServerConfig,
    logger: &Logger,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Wrap the read half for line-based async I/O
    let mut reader = BufReader::new(reader_half).lines();

    // Per-client rate limiter
    let rate_limiter = Arc::new(Mutex::new(RateLimiter::new(rate_limit)));

    // Run the central message loop (handles lines, flow-control, timeout, shutdown)
    if let Err(e) = handle_client_messages(
        &mut reader,
        &client,
        &clients,
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
        // Log any unexpected error during the session
        logger.warn(&format!("⚠️ Error in client {} session: {}", client.addr, e));
    }

    // Remove this client from the active list and compute how many remain
    let remaining = {
        let mut list = clients.write().await;
        list.retain(|c| c.addr != client.addr);
        list.len()
    };

    // Log the cleanup with the updated count
    logger.info(&format!(
        "🧼 Client {} removed; {} clients remain",
        client.addr, remaining
    ));

    Ok(())
}


/// === MESSAGES ===

/// Manages a single client session until disconnect or shutdown:
/// - Reads incoming lines and applies rate limiting & backpressure reporting  
/// - Responds to throttle and flow-control commands  
/// - Enforces inactivity timeouts  
/// - Sends a shutdown notice on server exit  
async fn handle_client_messages(
    // Line-based reader for this client's incoming data
    reader: &mut Lines<BufReader<tokio::net::tcp::OwnedReadHalf>>,
    // Metadata and writer handle for this client
    sender: &Client,
    // Shared list of all clients, used for broadcasting
    clients: &ClientList,
    // Receiver for the server's global shutdown signal
    shutdown_rx: &mut broadcast::Receiver<()>,
    // Channel to report our outgoing-queue length for backpressure
    backpressure_tx: &mpsc::Sender<(SocketAddr, usize)>,
    // How long before we drop an idle client
    timeout: Option<Duration>,
    // Per-client rate limiter
    rate_limiter: Arc<Mutex<RateLimiter>>,
    // All config settings
    config: &ServerConfig,
    // Logger for recording events
    logger: &Logger,
    // Receiver for throttle/flow-control commands targeted at this client
    mut control_rx: mpsc::Receiver<ClientCommand>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Channel for staging our queue-size reports
    let (queue_tx, mut queue_rx) = mpsc::channel::<usize>(config.queue_monitor_capacity);

    // Track when the client last sent anything
    let mut last_active = Instant::now();

    // Timer that fires every `timeout_check_interval_secs` to enforce inactivity
    let mut timeout_interval = time::interval(Duration::from_secs(
        config.timeout_check_interval_secs,
    ));

    // Main loop: handle whichever event happens first
    loop {
        tokio::select! {
            // 1) Incoming line from client
            maybe_line = reader.next_line() => {
                // Update last-active timestamp immediately
                last_active = Instant::now();

                match maybe_line {
                    Ok(Some(line)) => {
                        // A full line arrived: process rate-limit, logging, broadcast
                        process_client_line(
                            line,
                            sender,
                            clients,
                            &rate_limiter,
                            queue_tx.clone(),
                            logger,
                        ).await;
                    }
                    Ok(None) => {
                        // EOF: client closed connection cleanly
                        logger.info(&format!("⚠️ {} disconnected gracefully.", sender.addr));
                        break;
                    }
                    Err(e) => {
                        // I/O error reading from socket
                        logger.warn(&format!("❌ Error reading from {}: {}", sender.addr, e));
                        break;
                    }
                }
            }

            // 2) Throttle or flow-control command arrived
            Some(cmd) = control_rx.recv() => {
                // Pause message handling as requested
                apply_client_command(cmd, sender, config, logger).await;
            }

            // 3) It's time to report our queue size for backpressure
            Some(queue_size) = queue_rx.recv() => {
                // Clone what we need into a small async task to avoid blocking
                let addr = sender.addr;
                let tx = backpressure_tx.clone();
                let log = logger.clone();

                tokio::spawn(async move {
                    if let Err(e) = tx.try_send((addr, queue_size)) {
                        // If the channel is full, warn and drop the report
                        log.warn(&format!(
                            "⚠️ Backpressure channel full for {}: {}",
                            addr, e
                        ));
                    }
                });
            }

            // 4) Check for inactivity timeout
            _ = timeout_interval.tick() => {
                if let Some(timeout) = timeout {
                    if last_active.elapsed() > timeout {
                        // Send a warning to the client then break
                        logger.info(&format!(
                            "⏰ Client {} timed out after {:?} of inactivity",
                            sender.addr, timeout
                        ));
                        let mut w = sender.writer.lock().await;
                        let _ = w
                            .write_all(b"Warning: You have been disconnected due to inactivity.\n")
                            .await;
                        break;
                    }
                }
            }

            // 5) Global shutdown signal from the server
            _ = shutdown_rx.recv() => {
                // Notify client and exit
                logger.warn(&format!("🛑 {} disconnected due to shutdown.", sender.addr));
                let mut w = sender.writer.lock().await;
                let _ = w
                    .write_all(b"Warning: Server is shutting down.\n")
                    .await;
                break;
            }
        }
    }

    Ok(())
}

/// Responds to a throttle or flow-control command by pausing the client's processing
///
/// # Behavior
/// - `Throttle`: wait `throttle_delay_ms` before handling the next message  
/// - `FlowControl`: wait `flow_control_delay_ms` before continuing reads  
async fn apply_client_command(
    cmd: ClientCommand,
    sender: &Client,
    config: &ServerConfig,
    logger: &Logger,
) {
    match cmd {
        ClientCommand::Throttle => {
            logger.info(&format!("⏳ Throttling {}", sender.addr));
            // Sleep here yields the task, letting other clients proceed
            tokio::time::sleep(Duration::from_millis(config.throttle_delay_ms)).await;
        }
        ClientCommand::FlowControl => {
            logger.info(&format!("⏸️ Pausing {} for flow control", sender.addr));
            tokio::time::sleep(Duration::from_millis(config.flow_control_delay_ms)).await;
        }
    }
}

/// Handle one line of client input:
/// 1. Apply rate limiting (drop or warn if too fast)
/// 2. Log the incoming message once (without the trailing newline)
/// 3. Build a JSON payload for the message
/// 4. Broadcast it to all other clients
///
/// # Parameters
/// - `line`: the raw line read (may include `\n`)  
/// - `sender`: who sent it (for logging and filtering)  
/// - `clients`: current active clients to broadcast to  
/// - `rate_limiter`: per-client rate limiter  
/// - `queue_tx`: channel to report broadcast queue size (for backpressure)  
/// - `logger`: for recording events  
async fn process_client_line(
    line: String,
    sender: &Client,
    clients: &ClientList,
    rate_limiter: &Arc<Mutex<RateLimiter>>,
    queue_tx: mpsc::Sender<usize>,
    logger: &Logger,
) {
    // 1) Rate limit: reset & check in one call
    let within_limit = rate_limiter.lock().await.check_limit();
    if !within_limit {
        // Notify the client they're sending too fast, then skip broadcasting
        let mut w = sender.writer.lock().await;
        let _ = w
            .write_all(b"Warning: You are sending messages too quickly. Please slow down.\n")
            .await;
        return;
    }

    // 2) Log the cleaned-up message (no trailing newline)
    let clean = remove_last_newline(&line);
    logger.info(&format!("📨 From {}: {}", sender.addr, clean));

    // 3) Build a JSON string to send to other clients
    let payload = json!({
        "remote_addr": sender.addr,
        "content": clean
    })
    .to_string();

    // 4) Broadcast to everyone else
    broadcast_message(clients, sender, &payload, queue_tx, logger).await;
}

/// Removes the last `\n` from the string if present.
/// Returns a slice into `s` without allocating.
///
/// # Why
/// - We don't want double newlines when logging or embedding content.
/// - Using `strip_suffix` avoids allocations when there's nothing to trim.
fn remove_last_newline(s: &str) -> &str {
    s.strip_suffix('\n').unwrap_or(s)
}

/// Sends `msg` to every client except the sender, reporting queue size for backpressure:
async fn broadcast_message(
    clients: &ClientList,               // Shared list of all connected clients
    sender: &Client,                    // The client who sent the original message
    msg: &str,                          // The text payload to broadcast
    queue_tx: mpsc::Sender<usize>,      // Channel to report current broadcast queue size
    logger: &Logger,                    // Logger for warning on send failures
) {
    // 1) Take a quick snapshot of all other clients under a read lock.
    //    This lock is held only long enough to clone the necessary Client structs.
    let snapshot: Vec<Client> = {
        let guard = clients.read().await;
        guard
            .iter()
            .filter(|c| c.addr != sender.addr)  // Exclude the original sender
            .cloned()                           // Clone the Arc handles cheaply
            .collect()                         // Collect into a Vec for iteration
    };

    // 2) Report how many clients we're about to send to.
    //    Using try_send ensures we never block; if the channel is full, we drop the report.
    let _ = queue_tx.try_send(snapshot.len());

    // 3) Build the message bytes once, with a guaranteed trailing newline.
    //    Wrapping in Arc<Bytes> makes cloning zero-copy for each task.
    let msg_bytes = Arc::new(Bytes::from(ensure_trailing_newline(msg).into_owned()));

    // 4) For each client in our snapshot, spawn a small task to write asynchronously.
    //    This way, a slow or stalled client can't hold up the others.
    for client in snapshot {
        let writer = client.writer.clone();  // Clone Arc<Mutex<...>> handle
        let buf = msg_bytes.clone();         // Clone Arc<Bytes> pointer
        let addr = client.addr;              // Capture address for logging
        let log = logger.clone();            // Clone logger handle

        tokio::spawn(async move {
            // Lock this client's writer just long enough to send the bytes
            let mut w = writer.lock().await;
            if let Err(e) = w.write_all(&buf).await {
                // Warn if we can't send (client may have disconnected)
                log.warn(&format!("❌ Failed to send to client {}: {}", addr, e));
            }
        });
    }
}

/// Ensures the string ends with exactly one newline (`\n`).
/// Returns a `Cow<str>` so we only allocate when needed.
///
/// # Why
/// - When we broadcast messages, downstream writers expect each payload to end in `\n`.
/// - `Cow` lets us borrow `s` unchanged if it already ends with `\n`, saving allocations.
fn ensure_trailing_newline(s: &str) -> Cow<str> {
    if s.ends_with('\n') {
        // Already has a newline: borrow the original string
        Cow::Borrowed(s)
    } else {
        // Add a newline, allocating a new `String`
        Cow::Owned(format!("{s}\n"))
    }
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