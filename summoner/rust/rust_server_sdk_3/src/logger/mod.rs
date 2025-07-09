// Import local time formatting tools from chrono
use chrono::Local;

// Set the global log level (e.g., Debug, Info, Warn, Error)
use log::LevelFilter;

// Set up the dispatch builder for combining logger outputs
use fern::Dispatch;

// JSON handling for structured output & key filtering
use serde_json::Value as JsonValue;

// Standard I/O and filesystem operations (stdout, file creation)
use std::{fs, io};

// Used to initialize a static value only once in a thread-safe way
use std::sync::OnceLock;

// Import your `LoggerConfig` (parsed from the Python dict) for all user settings
use crate::config::LoggerConfig;

/// Given a raw JSON string and an optional whitelist of keys,
/// parse it, prune the nested `content` object by `keys`, then
/// re-serialize back to a string (for console/plain-text uses).
fn prune_content_string(raw: &str, keys: &Option<Vec<String>>) -> String {
    match serde_json::from_str::<JsonValue>(raw) {
        Ok(mut v) => {
            if let Some(map) = v.as_object_mut() {
                if let (Some(JsonValue::String(inner_raw)), Some(keys)) =
                    (map.get("content"), keys)
                {
                    if let Ok(mut inner_v) = serde_json::from_str::<JsonValue>(inner_raw) {
                        if let Some(inner_map) = inner_v.as_object_mut() {
                            inner_map.retain(|k, _| keys.contains(k));
                        }
                        let new_inner =
                            serde_json::to_string(&inner_v).unwrap_or_else(|_| inner_raw.clone());
                        map.insert("content".to_string(), JsonValue::String(new_inner));
                    }
                }
            }
            serde_json::to_string(&v).unwrap_or_else(|_| raw.to_string())
        }
        Err(_) => raw.to_string(),
    }
}

/// Given a raw JSON string and an optional whitelist of keys,
/// parse it, prune the nested `content` object by `keys`, then
/// return a `JsonValue` so it can be embedded unescaped.
fn prune_payload(raw: &str, keys: &Option<Vec<String>>) -> JsonValue {
    match serde_json::from_str::<JsonValue>(raw) {
        Ok(mut v) => {
            if let Some(map) = v.as_object_mut() {
                if let (Some(JsonValue::String(inner_raw)), Some(keys)) =
                    (map.get("content"), keys)
                {
                    if let Ok(mut inner_v) = serde_json::from_str::<JsonValue>(inner_raw) {
                        if let Some(inner_map) = inner_v.as_object_mut() {
                            inner_map.retain(|k, _| keys.contains(k));
                        }
                        let new_inner =
                            serde_json::to_string(&inner_v).unwrap_or_else(|_| inner_raw.clone());
                        map.insert("content".to_string(), JsonValue::String(new_inner));
                    }
                }
            }
            v
        }
        Err(_) => JsonValue::String(raw.to_string()),
    }
}

/// A simple Logger struct that wraps logging functions.
/// Clonable to allow use across multiple threads/tasks.
#[derive(Clone)]
pub struct Logger;

impl Logger {
    /// Logs a message at DEBUG level
    pub fn debug(&self, msg: &str) {
        log::debug!("{}", msg);
    }

    /// Logs a message at INFO level
    pub fn info(&self, msg: &str) {
        log::info!("{}", msg);
    }

    /// Logs a message at WARN level
    pub fn warn(&self, msg: &str) {
        log::warn!("{}", msg);
    }

    /// Logs a message at ERROR level
    pub fn error(&self, msg: &str) {
        log::error!("{}", msg);
    }
}

/// Static global LOGGER instance, initialized once
static LOGGER: OnceLock<Logger> = OnceLock::new();

/// Initialize the global logger exactly once, according to the provided settings.
/// After this call, all calls to `log::debug!(), info!(), warn!(), error!()` (and your
/// `Logger` methods) will go through the configured fern dispatcher.
pub fn init_logger(name: &str, cfg: &LoggerConfig) -> Logger {
    
    LOGGER.get_or_init(|| {
        // ────────────────────────────────────────────────────────────────
        // 1) Parse the configured level string into a log::LevelFilter
        //    If parsing fails, we default to Debug (most verbose).
        // ────────────────────────────────────────────────────────────────
        let level = cfg
            .log_level
            .parse::<LevelFilter>()
            .unwrap_or(LevelFilter::Debug);

        // ────────────────────────────────────────────────────────────────
        // 2) Build the base fern::Dispatch with the global minimum level
        // ────────────────────────────────────────────────────────────────
        let mut base = Dispatch::new().level(level);

        // ────────────────────────────────────────────────────────────────
        // 3) Console branch: if enabled, add a sub-dispatch that
        //    - filters nested JSON content by log_keys
        //    - formats with timestamp, name, level, message (with ANSI colors)
        //    - pipes output to stdout
        // ────────────────────────────────────────────────────────────────
        if cfg.enable_console_log {
            // Capture name + format string for the closure
            let nm = name.to_string();
            // let fmt = cfg.console_log_format.clone();
            let datefmt = cfg.date_format.clone();
            let enable_json = cfg.enable_json_log;
            let keys = cfg.log_keys.clone();

            // Format used for terminal logs
            let log_format_console = move |out: fern::FormatCallback, message: &std::fmt::Arguments, record: &log::Record| {
                // 1) Raw payload text
                let raw = message.to_string();

                // 2) Use our helper to prune nested content if JSON or whitelist is active
                let filtered = if enable_json || keys.is_some() {
                    prune_content_string(&raw, &keys)
                } else {
                    raw.clone()
                };

                // 3) Finally print to console with ANSI coloring
                out.finish(format_args!(
                    "\x1b[92m{}\x1b[0m - \x1b[94m{}\x1b[0m - {} - {}",
                    Local::now().format(&datefmt),
                    nm,
                    record.level(),
                    filtered
                ))
            };

            base = base.chain(
                Dispatch::new()
                    // The closure is called once per log record
                    .format(log_format_console)
                    .chain(io::stdout()), // write to console
            );
        }

        // ────────────────────────────────────────────────────────────────
        // 4) File branch: if enabled, add a sub-dispatch that
        //    a) optionally filters JSON payloads by `log_keys`
        //    b) emits either structured JSON or plain text lines
        //    c) writes to a file at "<log_file_path>/<name>.log"
        // ────────────────────────────────────────────────────────────────
        if cfg.enable_file_log {
            // Ensure the directory exists (no-op if empty or already present)
            if !cfg.log_file_path.is_empty() {
                let _ = fs::create_dir_all(&cfg.log_file_path);
            }

            // Clone into the closure
            let nm = name.to_string();
            // let fmt = cfg.log_format.clone();
            let datefmt = cfg.date_format.clone();
            let enable_json = cfg.enable_json_log;
            let keys = cfg.log_keys.clone();

            // Compute the logfile path
            let filepath = if cfg.log_file_path.is_empty() {
                format!("{}.log", nm.replace('.', "_"))
            } else {
                format!(
                    "{}/{}.log",
                    cfg.log_file_path,
                    nm.replace('.', "_")
                )
            };

            // Format used for file logs
            let log_format_file = move |out: fern::FormatCallback, message: &std::fmt::Arguments, record: &log::Record| {
                if enable_json {
                    // 1) Raw payload text
                    let raw = message.to_string();

                    // 2) Parse & prune nested "content" into a JsonValue
                    let pruned = prune_payload(&raw, &keys);

                    // 3) Build a real JSON envelope with "message" as an object
                    let envelope = serde_json::json!({
                        "timestamp": Local::now().format(&datefmt).to_string(),
                        "name":      nm,
                        "level":     record.level().to_string(),
                        "message":   pruned
                    });

                    // 4) Emit the envelope unescaped
                    out.finish(format_args!("{}", envelope))
                } else {
                    // Plain-text path
                    out.finish(format_args!(
                        "{} - {} - {} - {}",
                        Local::now().format(&datefmt),
                        nm,
                        record.level(),
                        message
                    ))
                }
            };

            // Attempt to open the logfile, but don’t panic—fallback to sink on error
            let file_output: Box<dyn io::Write + Send> = match fern::log_file(&filepath) {
                Ok(fh) => Box::new(fh),
                Err(err) => {
                    eprintln!("Warning: could not open log file {}: {}", filepath, err);
                    Box::new(io::sink())
                }
            };

            base = base.chain(
                Dispatch::new()
                    .format(log_format_file)
                    .chain(file_output), // write to Box<dyn Write + Send>
            );
        }

        // ────────────────────────────────────────────────────────────────
        // 5) Apply the composed dispatcher as the global logger
        //    Any subsequent log:: calls will use this configuration.
        // ────────────────────────────────────────────────────────────────
        base.apply().unwrap();

        // Return our zero-sized Logger handle
        Logger
    })
    .clone() // Give each caller a cheap, clonable reference
}
