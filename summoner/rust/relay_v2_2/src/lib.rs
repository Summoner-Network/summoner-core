use pyo3::{prelude::*, types::PyModule, Bound};

mod logger;
mod server;

use logger::get_logger;
use server::run_server;

#[pyfunction]
pub fn start_tokio_server(_py: Python, host: String, port: u16) -> PyResult<()> {
    let rt = tokio::runtime::Runtime::new().unwrap();
    let logger = get_logger("rust_server");

    rt.block_on(async {
        if let Err(e) = run_server(host, port, logger.clone()).await {
            logger.error(&format!("Rust server error: {}", e));
        }
    });

    Ok(())
}

#[pymodule]
fn relay_v2_2(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(start_tokio_server, m)?)?;
    Ok(())
}