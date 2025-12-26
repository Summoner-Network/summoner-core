use std::collections::HashMap;
use std::net::SocketAddr;
use tokio::sync::mpsc;

use crate::logger::Logger;
use crate::config::BackpressurePolicy;

/// Commands that the backpressure monitor can issue to the main server loop.
#[derive(Debug)]
pub enum BackpressureCommand {
    Throttle(SocketAddr),
    FlowControl(SocketAddr),
    Disconnect(SocketAddr),
}

#[derive(Debug, Clone, Copy)]
pub enum ClientCommand {
    Throttle,
    FlowControl,
}


/// Spawn a task to monitor backpressure from clients
pub fn spawn_backpressure_monitor(
    mut rx: mpsc::Receiver<(SocketAddr, usize)>,
    command_tx: mpsc::Sender<BackpressureCommand>,
    logger: Logger,
    policy: BackpressurePolicy,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {

        let mut client_queues: HashMap<SocketAddr, usize> = HashMap::new();

        while let Some((addr, queue_size)) = rx.recv().await {
            client_queues.insert(addr, queue_size);
            
            // Log if queue size is getting large
            if policy.enable_throttle && queue_size >= policy.throttle_threshold {
                logger.warn(&format!("⚠️ Throttling client {}: {} messages queued", addr, queue_size));

                if let Err(e) = command_tx.send(BackpressureCommand::Throttle(addr)).await {
                    logger.error(&format!("Failed to send throttle command: {}", e));
                }

            }

            // Log if queue size is getting large
            if policy.enable_flow_control && queue_size >= policy.flow_control_threshold {
                logger.warn(&format!("⏸️ Applying flow control to client {}: {} messages queued", addr, queue_size));
                
                if let Err(e) = command_tx.send(BackpressureCommand::FlowControl(addr)).await {
                    logger.error(&format!("Failed to send flow control command: {}", e));
                }

            }

            // Log if queue size is getting large
            if policy.enable_disconnect && queue_size >= policy.disconnect_threshold {
                logger.warn(&format!("🚨 Disconnecting client {} due to extreme backpressure: {} messages queued", addr, queue_size));
                
                if let Err(e) = command_tx.send(BackpressureCommand::Disconnect(addr)).await {
                    logger.error(&format!("Failed to send disconnect command: {}", e));
                }

            }

        }
    })
}