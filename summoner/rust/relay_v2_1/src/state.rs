use std::sync::Arc;
use tokio::net::tcp::OwnedWriteHalf;
use tokio::sync::Mutex;

pub type Client = Arc<Mutex<OwnedWriteHalf>>;
pub type ClientList = Arc<Mutex<Vec<Client>>>;
