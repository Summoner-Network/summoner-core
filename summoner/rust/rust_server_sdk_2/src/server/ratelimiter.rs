use tokio::time::{Instant, Duration};
use std::{collections::VecDeque, time::{SystemTime, UNIX_EPOCH}};

/// A generic rate-limiter interface.
/// `allow(amount)` returns `true` if `amount` units are permitted,
/// `false` if it would exceed the rate limit.
pub trait RateLimiter: Send {
    fn allow(&mut self, amount: u128) -> bool;
}

/* 
Fixed Window Limiter
---------------------
What it is:
A simple counter that resets every fixed interval (e.g., 60 seconds).

Good for:
- Very low overhead (just a counter and a timestamp).
- Predictable behavior when traffic is uniform.

Drawbacks / Gotchas:
- “Burstiness” at the boundary: you can send N requests at t=59s and another N at t=60s.
- Not ideal if you need smooth, continuous rate enforcement.
*/
pub struct FixedWindowLimiter {
    count: usize,
    window_start: Instant,
    max_per_minute: u32,
}

impl FixedWindowLimiter {
    pub fn new(max_per_minute: u32) -> Self {
        Self {
            count: 0,
            window_start: Instant::now(),
            max_per_minute,
        }
    }

    fn allow(&mut self, amount: u128) -> bool {
        let now = Instant::now();
        if now.duration_since(self.window_start) >= Duration::from_secs(60) {
            self.count = 0;
            self.window_start = now;
        }
        let n = amount as usize;
        self.count = self.count.saturating_add(n);
        self.count <= (self.max_per_minute as usize)
    }
}

impl RateLimiter for FixedWindowLimiter {
    fn allow(&mut self, amount: u128) -> bool {
        Self::allow(self, amount)
    }
}

/* 
Sliding Window Rate Limiter
--------------------------
What it is:
Keeps a timestamped queue of each event and evicts entries older than the window.

Good for:
- Precise tracking of events within any rolling interval.
- No boundary “spikes”—you can only ever send up to N events in any window.

Drawbacks / Gotchas:
- Memory grows with number of events (O(N) storage).
- Higher CPU overhead to purge old entries on each check.
*/
/// Sliding window limiter that aggregates counts per second.
/// Memory overhead O(window_secs) instead of O(event_count).
pub struct SlidingWindowRateLimiter {
    /// VecDeque of (unix_second, count) pairs
    buckets: VecDeque<(u64, usize)>,
    window: Duration,
    max_events: usize,
}

impl SlidingWindowRateLimiter {
    /// `max_events` over any rolling `window`
    pub fn new(max_events: usize, window: Duration) -> Self {
        Self {
            buckets: VecDeque::with_capacity(window.as_secs() as usize + 1),
            window,
            max_events,
        }
    }

    fn allow(&mut self, amount: u128) -> bool {
        // Current time, truncated to whole seconds
        let now_secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        // 1) Purge any buckets older than the window
        while let Some(&(ts, _)) = self.buckets.front() {
            if now_secs.saturating_sub(ts) > self.window.as_secs() {
                self.buckets.pop_front();
            } else {
                break;
            }
        }

        let n = amount as usize;
        // 2) Count events in remaining buckets
        let total_in_window: usize = self.buckets.iter().map(|&(_, cnt)| cnt).sum();

        // 3) If under limit, record these `n` events in the current-second bucket
        if total_in_window + n <= self.max_events {
            match self.buckets.back_mut() {
                // if last bucket is this exact second, just increment its count
                Some(back) if back.0 == now_secs => back.1 += n,
                // otherwise push a new one
                _ => self.buckets.push_back((now_secs, n)),
            }
            true
        } else {
            false
        }
    }
}

impl RateLimiter for SlidingWindowRateLimiter {
    fn allow(&mut self, amount: u128) -> bool {
        Self::allow(self, amount)
    }
}

/* 
Token Bucket Limiter
--------------------
What it is:
A “bucket” of tokens refilled at a steady rate; each request consumes tokens.

Good for:
- Smooth, burst-friendly rate limiting.
- Constant time checks and bounded memory (just a couple floats).

Drawbacks / Gotchas:
- Slightly more complex math (floating point).
- You must tune capacity vs. refill rate for your use case.
*/
pub struct TokenBucketLimiter {
    capacity: f64,
    tokens: f64,
    refill_rate_per_sec: f64,
    last_refill: Instant,
}

impl TokenBucketLimiter {
    /// `capacity`: max tokens you can accumulate (burst size)
    /// `refill_rate_per_sec`: how many tokens/sec are added
    pub fn new(capacity: f64, refill_rate_per_sec: f64) -> Self {
        Self {
            capacity,
            tokens: capacity,
            refill_rate_per_sec,
            last_refill: Instant::now(),
        }
    }

    fn allow(&mut self, amount: u128) -> bool {
        let now = Instant::now();
        let elapsed = now.duration_since(self.last_refill).as_secs_f64();
        self.tokens = (self.tokens + elapsed * self.refill_rate_per_sec).min(self.capacity);
        self.last_refill = now;

        let required = amount as f64;
        if self.tokens >= required {
            self.tokens -= required;
            true
        } else {
            false
        }
    }
}

impl RateLimiter for TokenBucketLimiter {
    fn allow(&mut self, amount: u128) -> bool {
        Self::allow(self, amount)
    }
}

/* 
Leaky Bucket Limiter
--------------------
What it is:
Queues requests and “leaks” them out at a constant drip rate; excess stays queued until capacity.

Good for:
- Smoothing out bursts by spacing requests evenly.
- Enforcing a hard queue size to prevent overload.

Drawbacks / Gotchas:
- Introduces latency under sustained load (queued requests wait).
- Requires tuning queue capacity vs. leak interval.
*/
pub struct LeakyBucketLimiter {
    queue: VecDeque<Instant>,
    capacity: usize,
    leak_interval: Duration,
}

impl LeakyBucketLimiter {
    /// `capacity`: max queued requests
    /// `leak_interval`: e.g. `Duration::from_millis(500)` for 2 req/sec
    pub fn new(capacity: usize, leak_interval: Duration) -> Self {
        Self {
            queue: VecDeque::with_capacity(capacity),
            capacity,
            leak_interval,
        }
    }

    fn allow(&mut self, amount: u128) -> bool {
        let now = Instant::now();
        // leak old requests
        if let Some(&oldest) = self.queue.front() {
            let elapsed = now.duration_since(oldest);
            let leaks = (elapsed.as_secs_f64() / self.leak_interval.as_secs_f64())
                .floor() as usize;
            for _ in 0..leaks {
                self.queue.pop_front();
            }
        }

        let n = amount as usize;
        if self.queue.len() + n <= self.capacity {
            for _ in 0..n {
                self.queue.push_back(now);
            }
            true
        } else {
            false
        }
    }
}

impl RateLimiter for LeakyBucketLimiter {
    fn allow(&mut self, amount: u128) -> bool {
        Self::allow(self, amount)
    }
}
