# Summoner Core SDK

<p align="center">
<img width="500px" src="assets/img/summoner_user_rounded_180.png" />
</p>

> The **Summoner Core SDK** is the foundation of the Summoner protocol, providing a minimal, composable runtime for building and coordinating autonomous agents.

This core SDK exposes the **hooks**, **communication layer**, and **execution model** needed to support decentralized identities, reputation-aware messaging, programmable automations, and orchestration.

## Documentation

The core codebase is thoroughly documented in our **official documentation**, available on our GitHub page [here](https://github.com/Summoner-Network/summoner-docs).

## Installation

Before running the platform, ensure that both **Python** and **Rust** are installed on your system. The `setup.sh` script will then take care of configuring the environment and compiling necessary components.

### 1. Install Python

The platform requires **Python 3.9+**. You can check if Python is installed using:

```bash
python3 --version
```

If it is not installed, download it from the [official Python website](https://www.python.org/downloads/) or install via your package manager.

#### On macOS:

```bash
brew install python
```

#### On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

### 2. Install Rust

The Rust-based servers depend on a working installation of the **Rust toolchain**.

Install Rust using `rustup`:

#### On macOS (via Homebrew):

```bash
brew install rustup
rustup-init
```

#### On macOS/Linux (via installer script):

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

After installation, restart your terminal and verify:

```bash
rustc --version     # ✅ Should print the Rust compiler version
cargo --version     # ✅ Should print the Cargo package manager version
```

### 3. Run the Setup Script

Once Python and Rust are installed, `git clone` the default branch, for example by using 
```bash
git clone https://github.com/Summoner-Network/summoner-core.git
```
and initialize the project environment by running:

```bash
source setup.sh
```

This script performs the following actions:

- Creates a **Python virtual environment** in the root directory (`venv`)
- Installs required **Python packages** listed in `requirements.txt`
- Generates a default **`.env`** file with configuration placeholders
- Installs all available **Rust server implementations**, using `Cargo.lock` to ensure consistent builds
- Installs the `summoner` folder as a **Python package** in editable mode, enabling clean imports like `from summoner.server import *` without modifying `PYTHONPATH`


### 4. Configure Environment Variables

The `.env` file defines key runtime parameters such as logging and database connection. You may need to adjust it to match your local setup:

```dotenv
# .env
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
```

After editing `.env`, make sure these values are correctly read by the Python settings module (`summoner/settings.py`). It uses `os.getenv()` to load defaults:

```python
# summoner/settings.py
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
SECRET_KEY = os.getenv("SECRET_KEY", "devsecret")
```

At this point, your development environment should be fully configured and ready to use. You can now launch a server or run clients to explore and test the code.


## Quick smoke tests

Use the ready-made scripts `open_server.sh` and `open_client.sh` in the repo root to verify your install. 

**POSIX (macOS/Linux)**

```bash
# terminal A
source venv/bin/activate
bash open_server.sh

# terminal B
source venv/bin/activate
bash open_client.sh
```

**Windows**
Use **Git Bash** (or WSL). Activate the venv then run the scripts with Bash:

```bash
source venv/Scripts/activate
bash ./open_server.sh   # terminal A
bash ./open_client.sh   # terminal B
```

If needed: `chmod +x open_server.sh open_client.sh`.

**Expected**
Server starts and listens and client connects and can send messages through a chat interactive window. If anything fails, re-run `source setup.sh` and try again.


## Contributions

This repository is open source for visibility and usage. External developers are welcome to [open issues](../../issues) to report bugs, suggest improvements, or request new features.

Direct code contributions to this repository are limited to internal team members.

If you would like to extend the project, we encourage you to explore our [documentation](https://github.com/Summoner-Network/summoner-docs/) on building modules and to use the [SDK template repository](https://github.com/Summoner-Network/summoner-sdk) as a starting point for creating safe, compatible extensions.

