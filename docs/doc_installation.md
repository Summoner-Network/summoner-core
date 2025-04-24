# Installation

Before running the platform, ensure that both **Python** and **Rust** are installed on your system. The `setup.sh` script will then take care of configuring the environment and compiling necessary components.

---

## 1. Install Python

The platform requires **Python 3.8+**. You can check if Python is installed using:

```bash
python3 --version
```

If it is not installed, download it from the [official Python website](https://www.python.org/downloads/) or install via your package manager.

### On macOS:

```bash
brew install python
```

### On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

---

## 2. Install Rust

The Rust-based servers depend on a working installation of the **Rust toolchain**.

Install Rust using `rustup`:

### On macOS (via Homebrew):

```bash
brew install rustup
rustup-init
```

### On macOS/Linux (via installer script):

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

After installation, restart your terminal and verify:

```bash
rustc --version     # ✅ Should print the Rust compiler version
cargo --version     # ✅ Should print the Cargo package manager version
```

---

## 3. Run the Setup Script

Once Python and Rust are installed, set up the project by running:

```bash
bash setup.sh
```

This script performs the following tasks:

- Creates a **Python virtual environment** in the root directory (`venv`)
- Installs required **Python packages** listed in `requirements.txt`
- Installs all available **Rust server implementations**, using `Cargo.lock` to ensure consistent versions
- Creates a base **`.env`** file for environment configuration

---

## 4. Configure Environment Variables

The `.env` file defines key runtime parameters such as logging and database connection. You may need to adjust it to match your local setup:

```dotenv
# .env
LOG_LEVEL=INFO
ENABLE_CONSOLE_LOG=true
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
```

After editing `.env`, make sure these values are correctly read by the Python settings module (`summoner/settings.py`). It uses `os.getenv()` to load defaults:

```python
# summoner/settings.py
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
ENABLE_CONSOLE_LOG = os.getenv("ENABLE_CONSOLE_LOG", "true").lower() == "true"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
SECRET_KEY = os.getenv("SECRET_KEY", "devsecret")
```

---

At this point, your development environment should be fully configured and ready to use. You can now launch the server or begin contributing code. For more details, refer to the [contribution guide](doc_contribute_to_server.md).