use fern::colors::{Color, ColoredLevelConfig};
use log::{LevelFilter};
use chrono::Local;
use std::sync::Arc;
use std::sync::Mutex as StdMutex;

#[derive(Clone)]
pub struct Logger {
    _guard: Arc<StdMutex<()>>,
}

impl Logger {
    pub fn new(name: &str) -> Self {
        static INIT: std::sync::Once = std::sync::Once::new();

        let name_clean = name.replace('.', "_");
        let log_file = format!("{}.log", name_clean);

        INIT.call_once(|| {
            let colors = ColoredLevelConfig::new()
                .info(Color::Green)
                .warn(Color::Yellow)
                .error(Color::Red)
                .debug(Color::Blue);

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
                .chain(fern::log_file(log_file).unwrap())
                .apply();
        });

        Logger {
            _guard: Arc::new(StdMutex::new(())),
        }
    }

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
