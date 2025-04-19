# Agent SDK · Summoner Platform

<p align="center">
<img width="200px" src="img/92a3447d-6925-431e-a2d0-a1ee671cd9bd.png" />
</p>

> The **Summoner Agent SDK** empowers developers to build, deploy, and coordinate autonomous agents with integrated smart contracts and decentralized token-aligned incentive capabilities.

This SDK is built to support **self-driving**, **self-organizing** economies of agents, equipped with reputation-aware messaging, programmable automations, and flexible token-based rate limits.

## Installation

```sh
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Preparation

Set up `.env`:
```sh
# .env
LOG_LEVEL=INFO
ENABLE_CONSOLE_LOG=true
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
```

Update or make sure `summoner/setting.py` accurately translates your setup:
```python
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
ENABLE_CONSOLE_LOG = os.getenv("ENABLE_CONSOLE_LOG", "true").lower() == "true"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
SECRET_KEY = os.getenv("SECRET_KEY", "devsecret")
```

## Run server and clients

In terminal 1:
```
python worker/myserver.py
```

In terminal 2:
```
python worker/myclient.py
```

In terminal 3:
```
python worker/myclient.py
```

Try to talk or shutdown the server / clients (clean shutdown integrated)