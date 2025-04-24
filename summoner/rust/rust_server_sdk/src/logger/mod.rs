// Import color configuration for pretty terminal output using fern
use fern::colors::{Color, ColoredLevelConfig};

// Import local time formatting tools from chrono
use chrono::Local;

// Set the global log level (e.g., Debug, Info, Warn, Error)
use log::LevelFilter;

// Used to initialize a static value only once in a thread-safe way
use std::sync::OnceLock;

/// A simple Logger struct that wraps logging functions.
/// Clonable to allow use across multiple threads/tasks.
#[derive(Clone)]
pub struct Logger;

impl Logger {
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

/// Initializes and returns a global logger with:
/// - Colored console output
/// - Timestamped and named file logging
/// - Caller provides a logger name for context (used in output formatting)
pub fn get_logger(name: &str) -> Logger {
    // Duplicate the name for use in both file and console format closures
    let name_owned_1 = name.to_string();
    let name_owned_2 = name.to_string();

    // Initialize the logger if it hasn't been initialized yet
    LOGGER.get_or_init(|| {
        // Configure custom colors per log level (only used for console)
        let _colors = ColoredLevelConfig::new()
            .info(Color::Green)
            .warn(Color::Yellow)
            .error(Color::Red)
            .debug(Color::Blue)
            .trace(Color::Magenta);

        // Sanitize the logger name to use in the log filename
        let safe_name = name.replace('.', "_");
        let file_name = format!("{}.log", safe_name);

        // Format used for file logs:
        // [timestamp] - [logger name] - [log level] - [message]
        let log_format_file = move |out: fern::FormatCallback, message: &std::fmt::Arguments, record: &log::Record| {
            out.finish(format_args!(
                "{} - {} - {} - {}",
                Local::now().format("%Y-%m-%d %H:%M:%S%.3f"),
                name_owned_1,
                record.level(),
                message
            ))
        };

        // Format used for terminal logs:
        // Includes ANSI escape codes for colored output
        let log_format_console = move |out: fern::FormatCallback, message: &std::fmt::Arguments, record: &log::Record| {
            out.finish(format_args!(
                "\x1b[92m{}\x1b[0m - \x1b[94m{}\x1b[0m - {} - {}",
                Local::now().format("%Y-%m-%d %H:%M:%S%.3f"),
                name_owned_2,
                record.level(),
                message
            ))
        };

        // Set up a composite logger:
        // - One branch logs to console with formatting
        // - Another logs to file with formatting
        fern::Dispatch::new()
            .level(LevelFilter::Debug) // Log everything at DEBUG and above
            .chain(
                fern::Dispatch::new()
                    .format(log_format_console)
                    .chain(std::io::stdout()) // Print to terminal
            )
            .chain(
                fern::Dispatch::new()
                    .format(log_format_file)
                    .chain(fern::log_file(file_name).unwrap()) // Save to log file
            )
            .apply() // Activate the logger
            .unwrap();

        // Return the logger instance
        Logger
    }).clone() // Clone so each caller gets a usable reference
}
