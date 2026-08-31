#!/bin/sh
set -e

# SSH to the SAP server, if a key was mounted in (docker-compose.sap-ssh.yml). Only the
# catalogue sync's `ssh` transport uses it; with no SAP_SSH_KEY this is a no-op and the
# container holds no credentials at all, which is the default and the normal case.
#
# The key is copied rather than read where it is mounted because OpenSSH refuses a
# private key that others can read, and a Windows bind mount arrives inside the
# container as 0777 whatever it looks like on the host - "bad permissions" is what a
# sync would report instead.
if [ -n "$SAP_SSH_KEY" ] && [ -f "$SAP_SSH_KEY" ]; then
  echo "Preparing SSH access to ${SAP_SSH_HOSTNAME:-the SAP server}..."
  mkdir -p /root/.ssh
  chmod 700 /root/.ssh
  cp "$SAP_SSH_KEY" /root/.ssh/id_sap
  chmod 600 /root/.ssh/id_sap
  # With the host's known_hosts mounted too, the server is verified as usual. Without
  # it, accept-new trusts whatever answers first - which is why mounting it is what the
  # override file does.
  if [ -n "$SAP_SSH_KNOWN_HOSTS" ] && [ -f "$SAP_SSH_KNOWN_HOSTS" ]; then
    cp "$SAP_SSH_KNOWN_HOSTS" /root/.ssh/known_hosts
    chmod 600 /root/.ssh/known_hosts
    strict=yes
  else
    strict=accept-new
  fi
  cat > /root/.ssh/config <<SSH_CONFIG
Host ${SAP_SSH_HOST:-ebserver}
  HostName ${SAP_SSH_HOSTNAME:-192.168.0.113}
  User ${SAP_SSH_USER:-Administrator}
  IdentityFile /root/.ssh/id_sap
  IdentitiesOnly yes
  StrictHostKeyChecking $strict
SSH_CONFIG
  chmod 600 /root/.ssh/config
fi

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
