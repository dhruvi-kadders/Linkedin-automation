from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
CONFIG_ENV_PATH = BACKEND_DIR / "config" / ".env"

load_dotenv(CONFIG_ENV_PATH)

APP_HOST = os.getenv("HOST", "0.0.0.0")
APP_PORT = int(os.getenv("PORT", "3000"))

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "linkedin_bot")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

HEADLESS = os.getenv("HEADLESS", "").lower() == "true"
MAX_CONCURRENT_WORKERS = int(os.getenv("MAX_CONCURRENT_WORKERS", "15"))

FRONTEND_DIR = PROJECT_DIR / "frontend"
UPLOADS_DIR = BACKEND_DIR / "uploads"
RESUME_UPLOAD_DIR = UPLOADS_DIR / "resumes"
SCHEMA_PATH = BACKEND_DIR / "src" / "db" / "schema.sql"
