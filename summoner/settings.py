# settings.py
import os
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
SECRET_KEY = os.getenv("SECRET_KEY", "devsecret")
