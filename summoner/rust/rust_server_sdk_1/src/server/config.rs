use std::time::Duration;

/// Configuration options for the server
pub struct ServerConfig {
    /// Host address to bind to
    pub host: String,
    
    /// Port number to listen on
    pub port: u16,
    
    /// Maximum number of pending connections
    pub connection_buffer_size: usize,
    
    /// Timeout for inactive clients
    pub client_timeout: Duration,
    
    /// Rate limit for clients (messages per minute)
    pub rate_limit: u32
}