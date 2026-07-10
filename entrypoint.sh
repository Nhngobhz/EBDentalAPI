#!/bin/sh
set -e

echo "Waiting for the database to accept connections..."
until python -c "
import sys
from sqlalchemy import create_engine
from app.config import settings
try:
    create_engine(settings.DATABASE_URL).connect().close()
except Exception as exc:
    print(exc)
    sys.exit(1)
" 2>/dev/null; do
  sleep 1
done

echo "Running Alembic migrations..."
alembic upgrade head

echo "Starting Uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
