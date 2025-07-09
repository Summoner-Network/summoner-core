// Import everything we need from PyO3 so we can expose Rust functions to Python.
// - `prelude::*` gives us the common traits and types.
// - `PyModule` and `PyDict` let us work with Python modules and dicts.
// - `Bound` helps us hold a reference into Python data safely.
use pyo3::{prelude::*, types::{PyModule, PyDict}, Bound};

// Public module for parsing and validating server configuration provided by Python.
pub mod config;

// Public module exposing logging utilities for exchanged messages and server lifecycle events.
pub mod logger;

// Internal module implementing the TCP server protocol.
mod server;

// Pull in the specific items we need so we don’t have to write full paths later.
use logger::init_logger;
use server::run_server;
use config::ServerConfig;

/// Expose this Rust function to Python as `start_tokio_server(name, config)`.
/// Responsibilities:
/// 1. Read the Python config into Rust types.
/// 2. Build a Tokio runtime (async engine).
/// 3. Set up logging.
/// 4. Launch the async server without blocking Python’s threads.
///
/// Parameters:
/// - `_py`: a handle to the Python interpreter (needed for GIL management).
/// - `name`: a string to tag the logger.
/// - `config`: a Python dict of settings like host, port, timeouts.
///
/// Returns:
/// - `Ok(())` if everything starts fine.
/// - A Python `RuntimeError` if something goes wrong.
#[pyfunction]
pub fn start_tokio_server(_py: Python<'_>, name: String, config: Bound<PyDict>) -> PyResult<()> {
    // Convert the Python dict into our `ServerConfig` Rust struct.
    // If any required field is missing or has the wrong type, this returns an error.
    let server_config = ServerConfig::try_from(&config)?;

    // Build a multi-threaded Tokio runtime based on the `worker_threads` value.
    // This runtime drives all our async I/O and timers.
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(server_config.worker_threads)   // how many threads to use
        .thread_name("rust-server-worker")               // helpful for debugging
        .enable_all()                                    // turn on I/O, timers, signals
        .build()
        .map_err(|e|                                     // translate build errors into Python errors
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to build Tokio runtime: {}", e)
            )
        )?;

    // Create or retrieve a logger instance per the config
    let logger = init_logger(&name, &server_config.logger);

    // Run the server inside the Tokio runtime without holding Python’s Global Interpreter Lock.
    // This lets Python handle other things (like signals) while our Rust code runs.
    let server_result = _py.allow_threads(|| {
        rt.block_on(async {
            // Call our main server loop.
            match run_server(server_config, logger.clone()).await {
                Ok(()) => Ok(()),
                Err(e) => {
                    // If the server returns an error, log it in Rust.
                    logger.error(&format!("Rust server error: {}", e));
                    Err(e)
                }
            }
        })
    });

    // If the server returned an error, convert it into a Python RuntimeError.
    server_result.map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            format!("Server execution failed: {}", e)
        )
    })?;

    // Everything went smoothly. Tell Python we’re done.
    Ok(())
}

/// Define a Python module named `rust_server_sdk_3`.
/// Inside, register the `start_tokio_server` function so Python can import it.
#[pymodule]
fn rust_server_sdk_3(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Make our Rust function available in the module’s namespace.
    m.add_function(wrap_pyfunction!(start_tokio_server, m)?)?;

    // Module setup succeeded.
    Ok(())
}
