# SDK v2: completed

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
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
```

Update or make sure setting.py accurately translates your setup:
```python
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
SECRET_KEY = os.getenv("SECRET_KEY", "devsecret")
```

## Run server and clients

In terminal 1:
```
python dev/v2/worker/myserver.py
```

In terminal 2:
```
python dev/v2/worker/myclient.py
```

In terminal 3:
```
python dev/v2/worker/myclient.py
```

Try to talk or shutdown the server / clients (clean shutdown integrated)