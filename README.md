<p align="center">
  <img src="assets/hero-banner.png" alt="Claude Accounts Manager" width="800" />
</p>

<h1 align="center">Claude Accounts Manager</h1>

<p align="center">
  <strong>Multiple Claude Code accounts. One shared <code>~/.claude</code>. Zero duplication.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-3b82f6?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/encryption-Fernet_AES--128-22c55e?style=flat-square&logo=letsencrypt&logoColor=white" alt="Fernet AES-128"/>
  <img src="https://img.shields.io/badge/storage-SQLite-c97b5a?style=flat-square&logo=sqlite&logoColor=white" alt="SQLite"/>
  <img src="https://img.shields.io/badge/license-MIT-6366f1?style=flat-square" alt="MIT License"/>
</p>

---

## The Problem

Using Claude Code with multiple accounts means duplicating your entire `~/.claude` directory. Each copy needs its own config, plugins, MCP servers, and skills. Update one? Update them all.

## The Solution

Claude Accounts Manager stores all your credentials encrypted in a single SQLite database. When you launch Claude, the right credential is decrypted and injected as an environment variable. One `~/.claude` directory, shared by all accounts.

```
Before:                              After:
~/.claude-perso/                     ~/.claude/              (one, shared)
~/.claude-boulot/                    ~/.claude-accounts/
~/.claude-client/                      accounts.db           (encrypted)
  duplicated config everywhere         .key                  (Fernet key)
  plugins reinstalled 3x
                                     claude-perso   injects ANTHROPIC_API_KEY
                                     claude-boulot  injects CLAUDE_CODE_OAUTH_TOKEN
                                       same config, same plugins, same skills
```

<p align="center">
  <img src="assets/architecture.png" alt="Architecture" width="700" />
</p>

## Features

- **Encrypted storage** -- Fernet AES-128 encryption, `chmod 600` on database and key files
- **API Key + OAuth** -- Supports both `ANTHROPIC_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN`
- **OAuth capture** -- Run `claude auth login`, then capture tokens from `.credentials.json`
- **OAuth refresh** -- One-click token refresh from the dashboard, CLI, or auto-refresh on launch
- **Web terminal** -- Launch Claude directly from the browser with xterm.js + WebSocket
- **Shell aliases** -- Auto-generated `claude-perso`, `claude-boulot` aliases
- **Web dashboard** -- Visual management with health monitoring
- **CLI** -- Full-featured command-line interface
- **Export/Import** -- Backup and restore your accounts as JSON
- **Token monitoring** -- Track OAuth expiration, get alerts when tokens need refresh
- **Authentication** -- Password-protected dashboard with session cookies and API token support
- **Docker** -- Dockerfile + docker-compose.yml for one-command deployment
- **Auto-launch** -- Browser opens automatically when starting the server

## Installation

```bash
git clone git@github.com:ronylicha/claude-accounts.git
cd claude-accounts
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Requirements: Python 3.10+, `flask`, `cryptography`, `requests`, `flask-socketio`, `simple-websocket`.

> **Note:** The virtual environment must be activated (`source venv/bin/activate`) each time you open a new terminal before running any `python` command.

## Quick Start

### 1. Add an API Key Account

```bash
python cli.py add perso --key sk-ant-api03-xxxxx
```

### 2. Launch Claude with That Account

```bash
python cli.py launch perso
# Injects ANTHROPIC_API_KEY=sk-ant-api03-xxxxx and runs claude
```

### 3. Add a Second Account and Switch

```bash
python cli.py add boulot --key sk-ant-api03-yyyyy
python cli.py launch boulot    # different terminal, different account
```

That's it. Same `~/.claude`, same config, different credentials.

## Usage

### CLI Commands

| Command | Description |
|---------|-------------|
| `add <name> --key <key>` | Add an API key account |
| `add <name> --oauth` | Add an OAuth account (tokens captured later) |
| `login <name>` | Run OAuth login + capture tokens automatically |
| `refresh <name>` | Refresh an expired OAuth token using the refresh token |
| `launch <name>` | Launch Claude with the account's credentials (auto-refreshes if expired) |
| `list` | List all accounts with status |
| `status <name>` | Check token expiry for an account |
| `aliases` | Print generated shell aliases |
| `install` | Install aliases into `.bashrc` / `.zshrc` |
| `export` | Export all accounts as JSON |
| `import <file>` | Import accounts from JSON file |
| `serve [--remote] [--no-browser]` | Start the web dashboard (auth required, auto-opens browser) |

### OAuth Accounts

For Claude Pro/Team/Max accounts that use OAuth instead of API keys:

```bash
# Step 1: Authenticate with Claude (if not already done)
claude
# Auth happens automatically on first launch — complete it, then exit

# Step 2: Capture the tokens into an account
claude-accounts login client
# Reads tokens from ~/.claude/.credentials.json
# Stores them encrypted in SQLite
# Account is auto-created if it doesn't exist

# Step 3: Use it
claude-accounts launch client
# Injects CLAUDE_CODE_OAUTH_TOKEN and runs claude
```

### OAuth Token Refresh

OAuth tokens expire after a few hours. Claude Accounts Manager handles this automatically:

```bash
# Manual refresh from CLI
claude-accounts refresh client
# ✓ Token rafraîchi : sk-ant-oat01-...abc123
# ⏰ Expire dans : 3h59m

# Auto-refresh on launch (transparent)
claude-accounts launch client
# Token expired → auto-refreshes → launches claude
```

From the **web dashboard**:
- Click the **refresh icon** on any OAuth account to refresh its token
- Use **"Rafraîchir tous"** to batch-refresh all OAuth accounts
- Expired tokens are **auto-refreshed on page load**

The refresh token is single-use: each refresh returns a new one. Both `~/.claude-accounts/accounts.db` and `~/.claude/.credentials.json` are updated atomically.

### Shell Aliases

Generate aliases so you can type `claude-perso` instead of `python cli.py launch perso`:

```bash
# Install aliases into your shell (run once, from the venv)
python cli.py install
source ~/.claude-accounts/aliases.sh

# Now use directly — no venv activation needed:
claude-accounts list       # CLI wrapper auto-uses the venv
claude-accounts add work --key sk-ant-...
claude-perso               # launches with personal API key
claude-boulot              # launches with work OAuth token
claude-client              # launches with client credentials
```

## Web Dashboard

```bash
python cli.py serve                    # starts + opens browser
python cli.py serve --remote           # bind 0.0.0.0 for remote access
python cli.py serve --no-browser       # don't auto-open browser
python cli.py serve --port 8080        # custom port
```

Open http://localhost:5111

On first visit, you'll be asked to create a password. An API token is generated for programmatic access (shown once — save it).

<p align="center">
  <img src="assets/dashboard-preview.png" alt="Dashboard" width="700" />
</p>

The dashboard provides:

- **Authentication** -- Password-protected access with session cookies (7 days) and API token
- **Health monitoring** -- See which accounts are active, expired, or need login
- **One-click actions** -- Copy launch commands, capture OAuth tokens, refresh tokens
- **Web terminal** -- Launch Claude directly in the browser (xterm.js + WebSocket)
- **Auto-refresh** -- Expired OAuth tokens are refreshed automatically on load
- **Search & filter** -- Find accounts quickly
- **Keyboard shortcuts** -- `N` to add, `/` to search, `Ctrl+Esc` to close terminal

## How It Works

```
                    Credential Flow
                    ===============

  ┌──────────────┐     ┌───────────────┐     ┌──────────────┐
  │  API Key     │     │               │     │              │
  │  sk-ant-...  │────▶│  SQLite DB    │     │  claude      │
  │              │     │  (encrypted)  │────▶│  (env var    │
  ├──────────────┤     │               │     │   injected)  │
  │  OAuth Token │     │  Fernet       │     │              │
  │  sk-ant-oat  │────▶│  AES-128      │     │              │
  └──────────────┘     └───────────────┘     └──────────────┘

  Storage                Encryption            Runtime
  ~/.claude-accounts/    chmod 600             ANTHROPIC_API_KEY=xxx claude
  accounts.db            .key file             CLAUDE_CODE_OAUTH_TOKEN=yyy claude
```

### OAuth Token Lifecycle

When you run `claude-accounts login <name>`:

1. The CLI reads tokens from `~/.claude/.credentials.json` (created when you first run `claude`)
2. Encrypts the tokens and stores them in SQLite
3. On next `launch`, the access token is decrypted and injected as `CLAUDE_CODE_OAUTH_TOKEN`

When a token expires:

1. **Auto-refresh on launch** -- `get_launch_env()` detects expiry and calls `refresh_oauth_token()` automatically
2. **Manual refresh** -- `claude-accounts refresh <name>` or the dashboard refresh button
3. **Sync** -- `~/.claude/.credentials.json` is updated atomically after each refresh

> **Note:** You must run `claude` at least once to authenticate before capturing tokens. Auth happens automatically on first launch. Subsequent refreshes are handled automatically.

## Security

| Measure | Details |
|---------|---------|
| **Encryption** | Fernet AES-128 (symmetric, authenticated) |
| **Key storage** | `~/.claude-accounts/.key`, `chmod 600` |
| **Database** | `~/.claude-accounts/accounts.db`, `chmod 600` |
| **API response** | Credentials are masked in list endpoints |
| **Export** | `/api/export` returns decrypted data -- handle with care |
| **Aliases** | Contain credentials in plaintext (same security as `.bashrc` env vars) |
| **Auth password** | Hashed with werkzeug pbkdf2, never stored in plaintext |
| **API token** | SHA-256 hashed in database, shown only once at setup |
| **Session** | HttpOnly cookie, SameSite=Lax, 7-day expiry |
| **Rate limiting** | Login limited to 10 attempts per 15 minutes per IP |
| **WebSocket** | Authenticated via session cookie or API token |

Credentials are never stored in plaintext on disk except in Claude's own `.credentials.json` (which the CLI reads from, not writes to).

> **Remote access:** When using `--remote`, the server binds to `0.0.0.0`. Use an SSH tunnel or HTTPS reverse proxy for security. Never expose the plain HTTP port to the internet.

## API Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| `GET` | `/api/auth/status` | Check auth state (public) |
| `POST` | `/api/auth/setup` | Create admin password + get API token (public, one-time) |
| `POST` | `/api/auth/login` | Login with password (public) |
| `POST` | `/api/auth/logout` | Logout (clears session) |
| `POST` | `/api/auth/change-password` | Change password |
| `GET` | `/api/accounts` | List accounts (credentials masked) |
| `POST` | `/api/accounts` | Add an account |
| `PUT` | `/api/accounts/:id` | Update an account |
| `DELETE` | `/api/accounts/:id` | Delete an account |
| `GET` | `/api/accounts/:id/status` | Token status (valid, expired, needs login) |
| `POST` | `/api/accounts/:id/capture-oauth` | Capture tokens from `.credentials.json` |
| `POST` | `/api/accounts/:id/refresh-oauth` | Refresh OAuth token using refresh token |
| `POST` | `/api/accounts/:id/launch` | Get launch command with env vars |
| `GET` | `/api/generate-aliases` | Generated aliases script |
| `POST` | `/api/install-aliases` | Install aliases into shell |
| `GET` | `/api/export` | Export all accounts (decrypted) |
| `POST` | `/api/import` | Import accounts from JSON array |

> All API endpoints except `/api/auth/status`, `/api/auth/login`, and `/api/auth/setup` require authentication via session cookie or `X-Auth-Token` header.

### WebSocket Events (Terminal)

| Event | Direction | Description |
|-------|-----------|-------------|
| `start_terminal` | Client → Server | Start a terminal session with `{account_id}` |
| `terminal_started` | Server → Client | Terminal pty spawned successfully |
| `terminal_input` | Client → Server | Send keyboard input `{data}` |
| `terminal_output` | Server → Client | Terminal output `{data}` |
| `resize_terminal` | Client → Server | Resize pty `{rows, cols}` |
| `terminal_exit` | Server → Client | Process exited |
| `terminal_error` | Server → Client | Error message `{error}` |
| `stop_terminal` | Client → Server | Kill the terminal process |

## Docker

```bash
# Quick start (build + run + open browser)
./start.sh

# Or manually
docker compose up -d --build
# Open http://localhost:5111

# Logs
docker compose logs -f

# Stop
docker compose down
```

Data is persisted in `~/.claude-accounts` and `~/.claude` via Docker volumes.

## Exposing Externally

By default, the server listens on `127.0.0.1` (localhost only). To access it from another machine, you need to **bind to `0.0.0.0`** and ideally protect the connection with HTTPS.

> [!CAUTION]
> Never expose plain HTTP to the internet. Credentials transit over the wire — always use HTTPS or an SSH tunnel.

### 1. Quick: `--remote` Flag

Bind the server to all network interfaces:

```bash
# Without Docker
python cli.py serve --remote --port 5111

# With Docker (already the default in the Dockerfile)
docker compose up -d
```

The server is now reachable at `http://<your-server-ip>:5111` from your local network.

### 2. SSH Tunnel (Simplest Secure Method)

No server-side configuration needed. From your **local machine**, forward the port through SSH:

```bash
ssh -L 5111:localhost:5111 user@your-server
```

Then open `http://localhost:5111` locally. Traffic is encrypted through the SSH connection.

> [!TIP]
> Add `-N` (no shell) and `-f` (background) for a persistent tunnel:
> ```bash
> ssh -NfL 5111:localhost:5111 user@your-server
> ```

### 3. Reverse Proxy with HTTPS (Recommended for Production)

Use a reverse proxy to add TLS termination in front of the service.

#### Nginx + Let's Encrypt

```nginx
server {
    listen 443 ssl;
    server_name claude.example.com;

    ssl_certificate     /etc/letsencrypt/live/claude.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/claude.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5111;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support (required for the web terminal)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

server {
    listen 80;
    server_name claude.example.com;
    return 301 https://$host$request_uri;
}
```

```bash
# Get a certificate with certbot
sudo certbot --nginx -d claude.example.com
```

#### Caddy (Auto-HTTPS)

```
claude.example.com {
    reverse_proxy localhost:5111
}
```

Caddy handles TLS certificates automatically — no extra configuration needed.

### 4. Cloudflare Tunnel (No Open Ports)

Expose the service without opening any firewall ports:

```bash
# Install cloudflared
# https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

# Authenticate
cloudflared tunnel login

# Create a tunnel
cloudflared tunnel create claude-accounts

# Run the tunnel
cloudflared tunnel --url http://localhost:5111 run claude-accounts
```

Your service is accessible at `https://<tunnel-id>.cfargotunnel.com` (or a custom domain via Cloudflare DNS).

### 5. Docker Compose for Remote Access

To override the default port binding and expose on all interfaces:

```yaml
# docker-compose.override.yml
services:
  claude-accounts:
    ports:
      - "0.0.0.0:5111:5111"
```

To combine with a Caddy reverse proxy in Docker:

```yaml
# docker-compose.override.yml
services:
  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
    depends_on:
      - claude-accounts

  claude-accounts:
    ports: []  # remove direct exposure

volumes:
  caddy_data:
```

With a `Caddyfile`:
```
claude.example.com {
    reverse_proxy claude-accounts:5111
}
```

### Security Checklist

| Item | Details |
|------|---------|
| **HTTPS** | Always use TLS in production (reverse proxy, Cloudflare, or SSH tunnel) |
| **Password** | Set a strong dashboard password on first visit |
| **Firewall** | Only open ports 80/443 (or none with Cloudflare Tunnel) |
| **API token** | Use `X-Auth-Token` for programmatic access, never share it |
| **Rate limiting** | Login is limited to 10 attempts per 15 min per IP (built-in) |
| **Updates** | Keep the server behind a VPN or firewall if hosting sensitive credentials |

## Project Structure

```
claude-accounts/
  cli.py              CLI interface (add, login, refresh, launch, serve, ...)
  server.py           Flask API + WebSocket terminal (flask-socketio + pty)
  db.py               Database layer (SQLite + Fernet encryption + OAuth refresh)
  static/index.html   Web dashboard SPA (xterm.js terminal, token management)
  requirements.txt    flask, cryptography, requests, flask-socketio, simple-websocket
  Dockerfile          Docker image (python:3.12-slim + tini)
  docker-compose.yml  Docker orchestration with volumes
  start.sh            Quick start script (build + run + open browser)
  .dockerignore       Docker build exclusions
  assets/             README illustrations
```

## Contributing

Contributions are welcome. The codebase is intentionally simple -- three Python files and one HTML file.

To set up for development:

```bash
git clone git@github.com:ronylicha/claude-accounts.git
```
cd claude-accounts
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python server.py
```

## License

MIT
