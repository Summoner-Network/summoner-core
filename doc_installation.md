# Installation

The following command will create a python environment `venv`, set environement variables in `.env` and will install rust packages (`Cargo.lock`) for the different rust-server versions implemented.
```sh
bash setup.sh
```
The file `.env` might need to be updated with specific values:
```sh
# .env
LOG_LEVEL=INFO
ENABLE_CONSOLE_LOG=true
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
```
If the `.env` is updated, make sure that `summoner/setting.py` accurately translates your setup:
```python
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
ENABLE_CONSOLE_LOG = os.getenv("ENABLE_CONSOLE_LOG", "true").lower() == "true"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
SECRET_KEY = os.getenv("SECRET_KEY", "devsecret")
```

