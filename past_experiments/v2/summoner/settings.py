# settings.py
import os
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG") #priority levels: DEBUG < INFO < WARNING < ERROR < CRITICAL
ENABLE_CONSOLE_LOG = os.getenv("ENABLE_CONSOLE_LOG", "true").lower() == "true"

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
SECRET_KEY = os.getenv("SECRET_KEY", "devsecret")
