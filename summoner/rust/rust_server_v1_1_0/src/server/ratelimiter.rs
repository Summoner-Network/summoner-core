use tokio::time::{Duration, Instant};

/// This struct tracks client message rate for rate limiting
pub struct RateLimiter {
    /// Messages sent in the current window
    count: usize,
    /// Start of the current rate limiting window
    window_start: Instant,
    /// Maximum messages allowed per minute
    max_per_minute: u32,
}

impl RateLimiter {
    /// Create a new rate limiter with the specified messages per minute limit
    pub fn new(max_per_minute: u32) -> Self {
        Self {
            count: 0,
            window_start: Instant::now(),
            max_per_minute,
        }
    }

    /// Check if a new message would exceed the rate limit
    pub fn check_limit(&mut self) -> bool {
        let now = Instant::now();
        if now.duration_since(self.window_start) >= Duration::from_secs(60) {
            self.count = 0;
            self.window_start = now;
        }

        self.count += 1;
        self.count <= (self.max_per_minute as usize)
    }
}
