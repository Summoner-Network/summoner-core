use fern::colors::{Color, ColoredLevelConfig};
use chrono::Local;
use log::LevelFilter;
use std::sync::OnceLock;

#[derive(Clone)]
pub struct Logger;

impl Logger {
    pub fn info(&self, msg: &str) {
        log::info!("{}", msg);
    }

    pub fn warn(&self, msg: &str) {
        log::warn!("{}", msg);
    }

    pub fn error(&self, msg: &str) {
        log::error!("{}", msg);
    }
}

static LOGGER: OnceLock<Logger> = OnceLock::new();

pub fn get_logger(name: &str) -> Logger {
    let name_owned_1 = name.to_string();
    let name_owned_2 = name.to_string();

    LOGGER.get_or_init(|| {
        let _colors = ColoredLevelConfig::new()
            .info(Color::Green)
            .warn(Color::Yellow)
            .error(Color::Red)
            .debug(Color::Blue)
            .trace(Color::Magenta);

        let safe_name = name.replace('.', "_");
        let file_name = format!("{}.log", safe_name);

        let log_format_file = move |out: fern::FormatCallback, message: &std::fmt::Arguments, record: &log::Record| {
            out.finish(format_args!(
                "{} - {} - {} - {}",
                Local::now().format("%Y-%m-%d %H:%M:%S%.3f"),
                name_owned_1,
                record.level(),
                message
            ))
        };

        let log_format_console = move |out: fern::FormatCallback, message: &std::fmt::Arguments, record: &log::Record| {
            out.finish(format_args!(
                "\x1b[92m{}\x1b[0m - \x1b[94m{}\x1b[0m - {} - {}",
                Local::now().format("%Y-%m-%d %H:%M:%S%.3f"),
                name_owned_2,
                record.level(),
                message
            ))
        };

        fern::Dispatch::new()
            .level(LevelFilter::Debug)
            .chain(
                fern::Dispatch::new()
                    .format(log_format_console)
                    .chain(std::io::stdout())
            )
            .chain(
                fern::Dispatch::new()
                    .format(log_format_file)
                    .chain(fern::log_file(file_name).unwrap())
            )
            .apply()
            .unwrap();

        Logger
    }).clone()
} 
