// Import PyO3 to expose Rust code to Python
use pyo3::{prelude::*, types::PyModule, Bound};

// Import configuration struct
use std::time::Duration;

// Declare modules from this crate:
// - `logger` is public to the entire crate
// - `server` is private to lib.rs
pub mod logger;
mod server;

// Import public items from logger and server modules
use logger::get_logger;
use server::run_server;
use server::config::ServerConfig;

/// This function is exposed to Python to start the Rust async server:
/// - It creates the async Tokio runtime (like `asyncio.run` in Python)
/// - It initializes the logger using the given name
/// - It launches the server on the specified host and port
#[pyfunction]
pub fn start_tokio_server(
    _py: Python, 
    name: String, 
    host: String, 
    port: u16,
    connection_buffer_size: Option<usize>,
    client_timeout_secs: Option<u64>,
    rate_limit_msgs_per_minute: Option<u32>
) -> PyResult<()> {
    // Create a new Tokio async runtime with better error handling
    let rt = tokio::runtime::Runtime::new()
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to create Tokio runtime: {}", e)
            )
        })?;
    
    // Get a named logger instance
    let logger = get_logger(&name);
    
    // Create server configuration
    let config = ServerConfig {
        host, 
        port,
        connection_buffer_size: connection_buffer_size.unwrap_or(128),
        client_timeout: Duration::from_secs(client_timeout_secs.unwrap_or(300)), // 5 min default
        rate_limit: rate_limit_msgs_per_minute.unwrap_or(300) // 5 msgs/sec default
    };
    
    // Run the main async server inside the runtime
    rt.block_on(async {
        // Attempt to run the server and log any errors
        if let Err(e) = run_server(config, logger.clone()).await {
            logger.error(&format!("Rust server error: {}", e));
            return Err::<(), _>(e); // Propagate error within async context
        }
        Ok(())
    })
    .map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            format!("Server execution failed: {}", e)
        )
    })?;
    
    // Return success to Python
    Ok(())
}

/// This macro registers the module as a Python extension:
/// - It exposes the `start_tokio_server` function under the name `relay_v3`
#[pymodule]
fn rust_server_sdk_1(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Register the `start_tokio_server` function in the Python module
    m.add_function(wrap_pyfunction!(start_tokio_server, m)?)?;
    
    // Module setup was successful
    Ok(())
}