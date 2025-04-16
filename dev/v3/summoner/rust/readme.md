# guide

| Layer             | Language | Role |
|------------------|----------|------|
| **Agent SDK**    | Python   | User-defined logic, decorators, ML integration |
| **Protocol Layer** | Python   | Message formats, validation, session lifecycle |
| **Relay Layer**  | Rust     | TCP/WebSocket server, task scheduling, multi-agent routing |
| **Token Logic**  | Rust     | CAST token economy, secure transaction handling |
| **Bridge**       | FFI / gRPC / socket | Connect Python ↔ Rust (e.g. via JSON over pipe or shared protocol structs) |

Python remains the front-facing SDK — Rust powers the core communication backend and token transaction logic.

# Installations

Tools like [`pyo3`](https://pyo3.rs/) and [`maturin`](https://github.com/PyO3/maturin) generate Python packages **from compiled Rust code**, so:

- You write Rust files
- Use `maturin` or `setuptools-rust` to build a `.so` (Mac) or `.pyd` (Windows)
- Then you can `import rust_module` in Python like a normal package

---

### 🔧 How to install Rust on your MacBook Pro:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

or with [brew](https://formulae.brew.sh/formula/rust):

```
brew install rust
brew install rustup
```

Then restart your terminal and run:
```bash
rustc --version  # ✅ Should show version
cargo --version
```

This gives you:
- Rust compiler (`rustc`)
- Cargo (package manager)
- The build environment needed for `pyo3`

Then to use Rust in your Python project:

```bash
pip install maturin
```

You can now:
- Build Rust packages callable from Python
- Or wrap them in `.so` files and `import` them from Python

I’ll walk you through this when you’re ready.

---

# Links


