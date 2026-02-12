"""
Claude Accounts Manager — Database Layer
Stores all credentials (API keys + OAuth tokens) encrypted in a single SQLite.
At launch time, credentials are decrypted and injected as env vars.
No extra .claude dirs needed — one shared .claude for everyone.

OAuth tokens stored:
  - accessToken  (sk-ant-oat01-...)  → injected as CLAUDE_CODE_OAUTH_TOKEN
  - refreshToken (sk-ant-ort01-...)  → stored for refresh flow
  - expiresAt    (epoch ms)          → tracked for expiry warnings

API keys:
  - apiKey (sk-ant-api03-...)        → injected as ANTHROPIC_API_KEY
"""

import sqlite3
import os
import json
import time
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet
import requests

DB_DIR = Path.home() / ".claude-accounts"
DB_PATH = DB_DIR / "accounts.db"
KEY_PATH = DB_DIR / ".key"


# ── Encryption ────────────────────────────────────────────────────────────────

def _get_cipher():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists():
        key = KEY_PATH.read_bytes()
    else:
        key = Fernet.generate_key()
        KEY_PATH.write_bytes(key)
        os.chmod(str(KEY_PATH), 0o600)
    return Fernet(key)


def _encrypt(value: str) -> str:
    if not value:
        return ""
    return _get_cipher().encrypt(value.encode()).decode()


def _decrypt(token: str) -> str:
    if not token:
        return ""
    return _get_cipher().decrypt(token.encode()).decode()


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id              TEXT PRIMARY KEY,
            name            TEXT UNIQUE NOT NULL,
            auth_type       TEXT NOT NULL CHECK(auth_type IN ('api_key', 'oauth')),
            -- API key (encrypted)
            api_key_enc     TEXT NOT NULL DEFAULT '',
            -- OAuth tokens (encrypted)
            access_token_enc    TEXT NOT NULL DEFAULT '',
            refresh_token_enc   TEXT NOT NULL DEFAULT '',
            expires_at          INTEGER NOT NULL DEFAULT 0,
            -- Metadata
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            last_used        TEXT
        );
    """)
    conn.commit()
    conn.close()
    os.chmod(str(DB_PATH), 0o600)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def add_account(account_id: str, name: str, auth_type: str,
                api_key: str = "",
                access_token: str = "", refresh_token: str = "",
                expires_at: int = 0):
    conn = get_db()
    conn.execute(
        """INSERT INTO accounts
           (id, name, auth_type, api_key_enc, access_token_enc, refresh_token_enc, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            account_id, name, auth_type,
            _encrypt(api_key),
            _encrypt(access_token),
            _encrypt(refresh_token),
            expires_at,
        )
    )
    conn.commit()
    conn.close()


def update_account(account_id: str, **kwargs):
    conn = get_db()
    acc = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not acc:
        conn.close()
        raise ValueError(f"Account {account_id} not found")

    fields = {
        "name": kwargs.get("name", acc["name"]),
        "auth_type": kwargs.get("auth_type", acc["auth_type"]),
        "api_key_enc": _encrypt(kwargs["api_key"]) if "api_key" in kwargs else acc["api_key_enc"],
        "access_token_enc": _encrypt(kwargs["access_token"]) if "access_token" in kwargs else acc["access_token_enc"],
        "refresh_token_enc": _encrypt(kwargs["refresh_token"]) if "refresh_token" in kwargs else acc["refresh_token_enc"],
        "expires_at": kwargs.get("expires_at", acc["expires_at"]),
    }

    conn.execute(
        """UPDATE accounts SET name=?, auth_type=?, api_key_enc=?,
           access_token_enc=?, refresh_token_enc=?, expires_at=?
           WHERE id=?""",
        (fields["name"], fields["auth_type"], fields["api_key_enc"],
         fields["access_token_enc"], fields["refresh_token_enc"],
         fields["expires_at"], account_id)
    )
    conn.commit()
    conn.close()


def delete_account(account_id: str):
    conn = get_db()
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()


def get_account(account_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    conn.close()
    return _row_to_safe_dict(row) if row else None


def get_account_by_name(name: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM accounts WHERE name = ?", (name,)).fetchone()
    conn.close()
    return _row_to_safe_dict(row) if row else None


def list_accounts() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM accounts ORDER BY created_at").fetchall()
    conn.close()
    return [_row_to_safe_dict(r) for r in rows]


# ── Credential injection (the core!) ─────────────────────────────────────────

def get_launch_env(account_id: str) -> dict:
    """
    THE KEY FUNCTION.
    Decrypts credentials and returns env vars to inject when launching claude.

    For API key:  {"ANTHROPIC_API_KEY": "sk-ant-api03-..."}
    For OAuth:    {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-..."}

    Same shared .claude dir — just different env vars per session.
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Account {account_id} not found")

    conn.execute("UPDATE accounts SET last_used = datetime('now') WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()

    if row["auth_type"] == "api_key":
        api_key = _decrypt(row["api_key_enc"])
        if not api_key:
            raise ValueError(f"No API key stored for account {account_id}")
        return {"ANTHROPIC_API_KEY": api_key}

    else:  # oauth
        access_token = _decrypt(row["access_token_enc"])
        if not access_token:
            raise ValueError(f"No OAuth token for account {account_id}. Run: claude-accounts login {row['name']}")

        if row["expires_at"] > 0:
            now_ms = int(time.time() * 1000)
            if now_ms > row["expires_at"]:
                # Auto-refresh if refresh token is available
                if row["refresh_token_enc"]:
                    try:
                        result = refresh_oauth_token(account_id)
                        access_token = _decrypt(
                            get_db().execute(
                                "SELECT access_token_enc FROM accounts WHERE id = ?",
                                (account_id,)
                            ).fetchone()["access_token_enc"]
                        )
                    except Exception as e:
                        raise ValueError(
                            f"OAuth token expired and refresh failed for '{row['name']}': {e}. "
                            f"Run: claude-accounts login {row['name']}"
                        )
                else:
                    raise ValueError(
                        f"OAuth token expired for '{row['name']}' (no refresh token). "
                        f"Run: claude-accounts login {row['name']}"
                    )

        return {"CLAUDE_CODE_OAUTH_TOKEN": access_token}


def get_token_status(account_id: str) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    conn.close()
    if not row:
        return {"status": "not_found"}

    if row["auth_type"] == "api_key":
        return {"status": "ok" if row["api_key_enc"] else "missing", "type": "api_key"}

    has_token = bool(row["access_token_enc"])
    expires_at = row["expires_at"]
    now_ms = int(time.time() * 1000)

    if not has_token:
        return {"status": "needs_login", "type": "oauth"}
    if expires_at > 0 and now_ms > expires_at:
        return {"status": "expired", "type": "oauth", "has_refresh": bool(row["refresh_token_enc"])}

    remaining = int((expires_at - now_ms) / 60000) if expires_at > 0 else None
    return {"status": "ok", "type": "oauth", "remaining_min": remaining, "has_refresh": bool(row["refresh_token_enc"])}


# ── OAuth capture ─────────────────────────────────────────────────────────────

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


def capture_oauth_tokens(account_id: str, credentials_path: str = None) -> dict:
    """
    Read OAuth tokens from Claude's .credentials.json and store them encrypted.
    Called after user has authenticated by running `claude`.

    Flow:
      1. User runs `claude` (auth happens automatically on first launch)
      2. This function reads the resulting tokens from .credentials.json
      3. Stores them encrypted in our SQLite
      4. Now `claude-accounts launch <name>` injects the token via env var

    Returns token info (masked).
    """
    path = Path(credentials_path) if credentials_path else CREDENTIALS_PATH

    if not path.exists():
        raise FileNotFoundError(
            f"Credentials file not found: {path}\n"
            f"Run 'claude' first to authenticate, then capture the tokens."
        )

    with open(path) as f:
        data = json.load(f)

    oauth = data.get("claudeAiOauth", {})
    access_token = oauth.get("accessToken", "")
    refresh_token = oauth.get("refreshToken", "")
    expires_at = oauth.get("expiresAt", 0)

    if not access_token:
        raise ValueError("No accessToken found in credentials file. Login may have failed.")

    update_account(
        account_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )

    remaining = None
    if expires_at > 0:
        remaining = int((expires_at - time.time() * 1000) / 60000)

    return {
        "captured": True,
        "token_preview": f"sk-ant-oat01-...{access_token[-6:]}",
        "has_refresh": bool(refresh_token),
        "expires_in_min": remaining,
    }


# ── OAuth refresh ─────────────────────────────────────────────────────────

OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


def refresh_oauth_token(account_id: str) -> dict:
    """
    Refresh an expired OAuth token using the stored refresh token.
    The refresh token is single-use: a new one is returned each time.
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    conn.close()

    if not row:
        raise ValueError(f"Account {account_id} not found")
    if row["auth_type"] != "oauth":
        raise ValueError(f"Account {account_id} is not OAuth")

    refresh_token = _decrypt(row["refresh_token_enc"])
    if not refresh_token:
        raise ValueError(
            f"No refresh token for '{row['name']}'. "
            f"Run: claude-accounts login {row['name']}"
        )

    try:
        resp = requests.post(OAUTH_TOKEN_URL, json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        }, timeout=15)
    except requests.RequestException as e:
        raise ConnectionError(f"OAuth refresh request failed: {e}")

    if resp.status_code != 200:
        detail = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
        raise ValueError(
            f"OAuth refresh failed ({resp.status_code}): {detail}. "
            f"Re-authenticate: claude-accounts login {row['name']}"
        )

    data = resp.json()
    new_access = data.get("access_token", "")
    new_refresh = data.get("refresh_token", "")
    expires_in = data.get("expires_in", 0)

    if not new_access:
        raise ValueError("OAuth refresh response missing access_token")

    expires_at = int(time.time() * 1000) + (expires_in * 1000) if expires_in else 0

    update_account(
        account_id,
        access_token=new_access,
        refresh_token=new_refresh,
        expires_at=expires_at,
    )

    _sync_credentials_file(account_id)

    remaining_min = int(expires_in / 60) if expires_in else None
    return {
        "refreshed": True,
        "token_preview": f"sk-ant-oat01-...{new_access[-6:]}",
        "expires_in_min": remaining_min,
    }


def _sync_credentials_file(account_id: str):
    """
    Sync refreshed tokens back to ~/.claude/.credentials.json
    so Claude Code can use them directly.
    Writes atomically via temp file + rename.
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    conn.close()
    if not row or row["auth_type"] != "oauth":
        return

    access_token = _decrypt(row["access_token_enc"])
    refresh_token = _decrypt(row["refresh_token_enc"])
    expires_at = row["expires_at"]

    cred_path = CREDENTIALS_PATH
    data = {}
    if cred_path.exists():
        try:
            with open(cred_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

    data["claudeAiOauth"] = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at,
    }

    cred_dir = cred_path.parent
    cred_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(cred_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, str(cred_path))
        os.chmod(str(cred_path), 0o600)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ── Export / Import ───────────────────────────────────────────────────────────

def export_all() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM accounts ORDER BY created_at").fetchall()
    conn.close()
    result = []
    for row in rows:
        entry = {"name": row["name"], "auth_type": row["auth_type"]}
        if row["auth_type"] == "api_key":
            entry["api_key"] = _decrypt(row["api_key_enc"])
        else:
            entry["access_token"] = _decrypt(row["access_token_enc"])
            entry["refresh_token"] = _decrypt(row["refresh_token_enc"])
            entry["expires_at"] = row["expires_at"]
        result.append(entry)
    return result


def import_accounts(data: list[dict]) -> int:
    count = 0
    for entry in data:
        name = entry.get("name", "").strip()
        if not name or get_account_by_name(name):
            continue
        account_id = f"acc_{name}_{os.urandom(4).hex()}"
        add_account(
            account_id=account_id,
            name=name,
            auth_type=entry.get("auth_type", "api_key"),
            api_key=entry.get("api_key", ""),
            access_token=entry.get("access_token", ""),
            refresh_token=entry.get("refresh_token", ""),
            expires_at=entry.get("expires_at", 0),
        )
        count += 1
    return count


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_safe_dict(row) -> dict:
    d = {
        "id": row["id"],
        "name": row["name"],
        "auth_type": row["auth_type"],
        "created_at": row["created_at"],
        "last_used": row["last_used"],
        "expires_at": row["expires_at"],
    }

    if row["auth_type"] == "api_key":
        d["masked_key"] = _mask(row["api_key_enc"], "sk-ant-...{}")
    else:
        d["masked_key"] = _mask(row["access_token_enc"], "oat01-...{}")
        d["has_refresh"] = bool(row["refresh_token_enc"])
        if row["expires_at"] > 0:
            now_ms = int(time.time() * 1000)
            if now_ms > row["expires_at"]:
                d["token_status"] = "expired"
            else:
                d["token_status"] = "valid"
                d["expires_in_min"] = int((row["expires_at"] - now_ms) / 60000)
        else:
            d["token_status"] = "no_expiry"

    return d


def _mask(encrypted: str, template: str) -> str:
    if not encrypted:
        return ""
    try:
        val = _decrypt(encrypted)
        return template.format(val[-6:])
    except Exception:
        return template.format("***")
