use fern::colors::{Color, ColoredLevelConfig};
use chrono::Local;
use log::{LevelFilter};
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
    LOGGER.get_or_init(|| {
        let colors = ColoredLevelConfig::new()
            .info(Color::Green)
            .warn(Color::Yellow)
            .error(Color::Red)
            .debug(Color::Blue);

        let safe_name = name.replace('.', "_");
        let file_name = format!("{}.log", safe_name);

        let _ = fern::Dispatch::new()
            .format(move |out, message, record| {
                out.finish(format_args!(
                    "{} [{}] {}",
                    Local::now().format("%Y-%m-%d %H:%M:%S"),
                    record.level(),
                    message
                ))
            })
            .level(LevelFilter::Debug)
            .chain(
                fern::Dispatch::new()
                    .format(move |out, message, record| {
                        out.finish(format_args!(
                            "{} - {} - {}",
                            Local::now().format("%H:%M:%S"),
                            colors.color(record.level()),
                            message
                        ))
                    })
                    .chain(std::io::stdout())
            )
            .chain(fern::log_file(file_name).unwrap())
            .apply();

        Logger
    }).clone()
} 