# How to contribute to the code base for the rust server

## Intorduction

<p align="center">
<img width="500px" src="img/protocol_v1.png" />
</p>

## Preparation
Assume that you already have python. 

With `brew` on MacOS:
```bash
brew install rustup
```
or on MacOS and linux:
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```
Then restart your terminal and run:
```bash
rustc --version  # ✅ Should show version
cargo --version
```

## Setup

If not already done (see installation)
```
bash setup.sh
```

- Copy paste relay and choose name `relay_v<tag>`
- Change name in `Cargo.toml` and `lib.rs` at `#[pymodule]`
- run `reinstall_rust_server.sh -v <tag>`

===
- update `server.py`:
```python
import relay_v1_1 as rust1_1
...
"rust_v1_1": rust1_1.start_tokio_server,
```
- run server:
```bash
python server.py --option rust_v1_1
```

Important: update `setup.sh`:
```bash
bash reinstall_rust_server.sh rust_server_sdk
```

## How to add your code

Need to structure the dictionaries