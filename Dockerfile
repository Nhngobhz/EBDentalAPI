FROM python:3.12-slim

WORKDIR /app

# openssh-client is for the SAP catalogue sync's `ssh` transport only: it runs sqlcmd
# on the SAP server, because Windows authentication is the only login that database
# accepts. Useless without a key, which the image never carries - see
# docker-compose.sap-ssh.yml, the opt-in override that mounts one in.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p static/uploads && chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
