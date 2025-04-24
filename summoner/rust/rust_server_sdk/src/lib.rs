// Import PyO3 to expose Rust code to Python
use pyo3::{prelude::*, types::PyModule, Bound};

// Declare modules from this crate:
// - `logger` is public to the entire crate
// - `server` is private to lib.rs
pub mod logger;
mod server;

// Import public items from logger and server modules
use logger::get_logger;
use server::run_server;

/// This function is exposed to Python to start the Rust async server:
/// - It creates the async Tokio runtime (like `asyncio.run` in Python)
/// - It initializes the logger using the given name
/// - It launches the server on the specified host and port
#[pyfunction]
pub fn start_tokio_server(_py: Python, name: String, host: String, port: u16) -> PyResult<()> {
    // Create a new Tokio async runtime
    let rt = tokio::runtime::Runtime::new().unwrap();

    // Get a named logger instance
    let logger = get_logger(&name);

    // Run the main async server inside the runtime
    rt.block_on(async {
        // Attempt to run the server and log any errors
        if let Err(e) = run_server(host, port, logger.clone()).await {
            logger.error(&format!("Rust server error: {}", e));
        }
    });

    // Return success to Python
    Ok(())
}

/// This macro registers the module as a Python extension:
/// - It exposes the `start_tokio_server` function under the name `relay_v3`
#[pymodule]
fn rust_server_sdk(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Register the `start_tokio_server` function in the Python module
    m.add_function(wrap_pyfunction!(start_tokio_server, m)?)?;

    // Module setup was successful
    Ok(())
}
