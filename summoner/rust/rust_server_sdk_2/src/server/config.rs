// Bring in PyO3 traits and types so we can read from a Python dict
use pyo3::prelude::*;
use pyo3::types::PyDict;

// We need Duration to turn seconds into a Rust time value
use std::time::Duration;

// This helper tells us how many CPU cores are available
use num_cpus;

/////////////////////////
// BackpressurePolicy  //
/////////////////////////

/// Rules for when and how to slow down or drop misbehaving clients
#[derive(Debug, Clone)]
pub struct BackpressurePolicy {
    // If true, we inject a delay when a client sends too many messages
    pub enable_throttle: bool,
    // How many queued messages trigger throttling
    pub throttle_threshold: usize,

    // If true, we pause reading from a client when they're too chatty
    pub enable_flow_control: bool,
    // How many queued messages trigger flow control
    pub flow_control_threshold: usize,

    // If true, we outright disconnect a client under extreme load
    pub enable_disconnect: bool,
    // How many queued messages trigger a forced disconnect
    pub disconnect_threshold: usize,
}

//////////////////////
// ServerConfig     //
//////////////////////

/// All the settings our server needs, driven by the Python-side dict
#[derive(Debug, Clone)]
pub struct ServerConfig {
    /// IP or hostname to listen on (e.g. `"127.0.0.1"`)
    pub host: String,
    
    /// Port number (e.g. `8888`)
    pub port: u16,
    
    /// How many incoming connections we'll buffer before backpressure
    pub connection_buffer_size: usize,
    
    /// Seconds before we drop an idle client
    pub client_timeout: Option<Duration>,
    
    /// Max messages each client can send per minute
    pub rate_limit: u32,

    /// Size of the channel where backpressure commands arrive
    pub command_buffer_size: usize,

    /// Seconds a misbehaving client stays quarantined
    pub quarantine_cooldown_secs: u64,

    /// The throttling/flow-control/disconnect settings above
    pub backpressure_policy: BackpressurePolicy,

    /// Milliseconds to wait when throttling a client
    pub throttle_delay_ms: u64,

    /// Milliseconds to pause when applying flow control
    pub flow_control_delay_ms: u64,

    /// Seconds between checking for idle clients
    pub timeout_check_interval_secs: u64,

    /// Milliseconds to sleep after a failed accept
    pub accept_error_backoff_ms: u64,

    /// How many control commands we buffer per client
    pub control_channel_capacity: usize,

    /// How many queue-size reports we buffer per client
    pub queue_monitor_capacity: usize,

    /// Seconds between cleaning out old quarantine entries
    pub quarantine_cleanup_interval_secs: u64,

    /// How many Tokio worker threads to spin up (defaults to cpu-1)
    pub worker_threads: usize,
}

/////////////////////////////////////////////
// Converting from a Python dict into Rust //
/////////////////////////////////////////////

impl<'py> TryFrom<&Bound<'py, PyDict>> for ServerConfig {
    // We return a PyErr if something goes wrong parsing
    type Error = PyErr;

    fn try_from(dict: &Bound<'py, PyDict>) -> Result<Self, PyErr> {
        
        // Helper: look up `key` in the dict, or fall back to `default`.
        // If the Python value has the wrong type, we warn but still use `default`.
        fn extract_or<'py, T: FromPyObject<'py>>(
            dict: &'py Bound<'py, PyDict>,
            key: &str,
            default: T
        ) -> T {
            match dict.get_item(key) {
                // Key exists; try to convert it to Rust type T
                Ok(Some(value)) => match value.extract::<T>() {
                    Ok(val) => val,                 // Good conversion
                    Err(err) => {
                        eprintln!("Warning: '{}' has wrong type: {}", key, err);
                        default                      // Fall back
                    }
                },
                // Key missing or other error: just use default
                _ => default,
            }
        }

        // Read each setting, supplying a sensible default value
        let host                          = extract_or(dict, "host",                        "127.0.0.1".to_string());
        let port                          = extract_or(dict, "port",                        8888);
        let connection_buffer_size        = extract_or(dict, "connection_buffer_size",     128);
        
        let rate_limit                    = extract_or(dict, "rate_limit_msgs_per_minute", 300);
        let command_buffer_size           = extract_or(dict, "command_buffer_size",       32);
        let quarantine_cooldown_secs      = extract_or(dict, "quarantine_cooldown_secs",  300);
        let throttle_delay_ms             = extract_or(dict, "throttle_delay_ms",         200);
        let flow_control_delay_ms         = extract_or(dict, "flow_control_delay_ms",    1000);
        let timeout_check_interval_secs   = extract_or(dict, "timeout_check_interval_secs", 30);
        let accept_error_backoff_ms       = extract_or(dict, "accept_error_backoff_ms",  100);
        let control_channel_capacity      = extract_or(dict, "control_channel_capacity",  8);
        let queue_monitor_capacity        = extract_or(dict, "queue_monitor_capacity",    100);
        let quarantine_cleanup_interval_secs = extract_or(dict, "quarantine_cleanup_interval_secs", 60);

        // Optional client timeout
        let client_timeout = match dict.get_item("client_timeout_secs")? {
            Some(value) if value.is_none() => None,
            Some(value) => {
                match value.extract::<u64>() {
                    Ok(secs) => Some(Duration::from_secs(secs)),
                    Err(err) => {
                        eprintln!("Warning: 'client_timeout_secs' wrong type: {}", err);
                        Some(Duration::from_secs(300))
                    }
                }
            }
            None => Some(Duration::from_secs(300)),
        };

        // Default worker threads = #cores minus one, but at least one
        let worker_threads = extract_or(
            dict,
            "worker_threads",
            num_cpus::get().saturating_sub(1).max(1),
        );

        // Now handle the nested "backpressure_policy" dict, if provided
        let bp_dict: Bound<'py, PyDict> = match dict.get_item("backpressure_policy")? {
            // If it's a dict, use it
            Some(obj) => match obj.downcast::<PyDict>() {
                Ok(downcasted) => downcasted.clone(),
                // If it wasn't a dict, reuse the top-level dict to get defaults
                Err(_) => dict.clone(),
            },
            // No key: reuse the top-level dict for defaults
            None => dict.clone(),
        };

        // Build the BackpressurePolicy from keys inside bp_dict
        let backpressure_policy = BackpressurePolicy {
            enable_throttle       : extract_or(&bp_dict, "enable_throttle",    true),
            throttle_threshold    : extract_or(&bp_dict, "throttle_threshold", 100),
            enable_flow_control   : extract_or(&bp_dict, "enable_flow_control", true),
            flow_control_threshold: extract_or(&bp_dict, "flow_control_threshold", 300),
            enable_disconnect     : extract_or(&bp_dict, "enable_disconnect",  true),
            disconnect_threshold  : extract_or(&bp_dict, "disconnect_threshold", 500),
        };

        // Finally, assemble our ServerConfig struct and return it
        Ok(ServerConfig {
            host,
            port,
            connection_buffer_size,
            client_timeout,
            rate_limit,
            command_buffer_size,
            quarantine_cooldown_secs,
            backpressure_policy,
            throttle_delay_ms,
            flow_control_delay_ms,
            timeout_check_interval_secs,
            accept_error_backoff_ms,
            control_channel_capacity,
            queue_monitor_capacity,
            quarantine_cleanup_interval_secs,
            worker_threads,
        })
    }
}
