# Server

## Resources

* [Course](https://web.mit.edu/rust-lang_v1.25/arch/amd64_ubuntu1404/share/doc/rust/html/book/first-edition/the-stack-and-the-heap.html)

* [Index](https://doc.rust-lang.org/rust-by-example/index.html)

## Clients

### Client

#### `derive`

> The compiler is capable of providing basic implementations for some traits via the #[derive] attribute. These traits can still be manually implemented if a more complex behavior is required. See [docs](https://doc.rust-lang.org/rust-by-example/trait/derive.html?highlight=copy#derive)

* [Copy](https://doc.rust-lang.org/core/marker/trait.Copy.html)

* [Clone](https://doc.rust-lang.org/std/clone/trait.Clone.html)

#### `addr`

`SocketAddr` comes from `std::net`, which represents Ipv4 and Ipv6. See [docs](https://doc.rust-lang.org/std/net/index.html) and [explanations](https://doc.rust-lang.org/std/net/enum.SocketAddr.html). It relies on `IpAddr`, which provides `V4` and `V6`:
```rs
pub enum SocketAddr {
    V4(SocketAddrV4),
    V6(SocketAddrV6),
}
```

#### `writer`

`Arc` is a thread-safe reference-counting pointer. 'Arc' stands for 'Atomically Reference Counted'. See [docs](https://doc.rust-lang.org/std/sync/struct.Arc.html)

> The type `Arc<T>` provides shared ownership of a value of type T, allocated in the heap. Invoking clone on `Arc` produces a new `Arc` instance, which points to the same allocation on the heap as the source `Arc`, while increasing a reference count

`tokio::net::tcp` provides the `OwnedWriteHalf` which can control the closing and opening of writing operations for a client (think ban, quanrantine, etc.). See [docs](https://docs.rs/tokio/latest/tokio/net/tcp/index.html)

#### `control_rx`

> Rust provides asynchronous channels for communication between threads. Channels allow a unidirectional flow of information between two end-points: the Sender and the Receiver.

`mpsc::Sender<T>` will send an element of type `T` over the receiving side of the channel.

