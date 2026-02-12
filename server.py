"""
Claude Accounts Manager — Flask API
All credentials in SQLite, injected as env vars at launch time.
Single shared .claude dir.
"""

import os
import uuid
import shlex
import pty
import select
import signal
import struct
import fcntl
import termios
import threading
import glob
import time
import secrets
import webbrowser
from datetime import timedelta
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, Response, session
from flask_socketio import SocketIO, emit, disconnect

import db


# ── Flask secret key (persistent) ────────────────────────────────────────────

def _get_or_create_secret_key() -> str:
    secret_path = db.DB_DIR / ".flask_secret"
    if secret_path.exists():
        return secret_path.read_text().strip()
    key = secrets.token_hex(32)
    db.DB_DIR.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(key)
    os.chmod(str(secret_path), 0o600)
    return key


app = Flask(__name__, static_folder="static")
app.secret_key = _get_or_create_secret_key()
app.permanent_session_lifetime = timedelta(days=7)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Active terminal sessions: {sid: {fd, pid}}
_terminals = {}

# Rate limiting for login: {ip: [timestamps]}
_login_attempts = {}
_LOGIN_MAX = 10
_LOGIN_WINDOW = 900  # 15 minutes


# ── Auth middleware ───────────────────────────────────────────────────────────

_PUBLIC_PATHS = {"/api/auth/status", "/api/auth/login", "/api/auth/setup"}


@app.before_request
def auth_guard():
    # Static files and public auth endpoints
    path = request.path
    if path == "/" or not path.startswith("/api/"):
        return None
    if path in _PUBLIC_PATHS:
        return None

    # Check API token header
    token = request.headers.get("X-Auth-Token", "")
    if token and db.verify_api_token(token):
        return None

    # Check session cookie
    if session.get("authenticated"):
        return None

    return jsonify({"error": "Authentication required"}), 401


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    return jsonify({
        "setup_done": db.is_setup_done(),
        "authenticated": bool(session.get("authenticated")),
    })


@app.route("/api/auth/setup", methods=["POST"])
def api_auth_setup():
    d = request.json or {}
    password = d.get("password", "")
    if len(password) < 4:
        return jsonify({"error": "Password too short (min 4 chars)"}), 400
    try:
        api_token = db.setup_admin(password)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    session.permanent = True
    session["authenticated"] = True
    return jsonify({"api_token": api_token}), 201


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    ip = request.remote_addr or "unknown"
    now = time.time()
    # Rate limit
    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW]
    if len(attempts) >= _LOGIN_MAX:
        return jsonify({"error": "Too many attempts. Try again later."}), 429

    d = request.json or {}
    password = d.get("password", "")
    if not db.verify_password(password):
        attempts.append(now)
        _login_attempts[ip] = attempts
        return jsonify({"error": "Invalid password"}), 401

    # Clear attempts on success
    _login_attempts.pop(ip, None)
    session.permanent = True
    session["authenticated"] = True
    return jsonify({"status": "ok"})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.clear()
    return jsonify({"status": "logged_out"})


@app.route("/api/auth/change-password", methods=["POST"])
def api_auth_change_password():
    d = request.json or {}
    old = d.get("old_password", "")
    new = d.get("new_password", "")
    if len(new) < 4:
        return jsonify({"error": "New password too short (min 4 chars)"}), 400
    if not db.change_password(old, new):
        return jsonify({"error": "Current password is incorrect"}), 401
    return jsonify({"status": "password_changed"})


@app.route("/api/accounts", methods=["GET"])
def api_list():
    return jsonify(db.list_accounts())


@app.route("/api/accounts", methods=["POST"])
def api_add():
    d = request.json or {}
    name = d.get("name", "").strip().lower().replace(" ", "-")
    auth_type = d.get("auth_type", "api_key")

    if not name or len(name) < 2:
        return jsonify({"error": "Nom invalide (min 2 car.)"}), 400
    if db.get_account_by_name(name):
        return jsonify({"error": f"'{name}' existe déjà"}), 409

    aid = f"acc_{uuid.uuid4().hex[:8]}"

    if auth_type == "api_key":
        api_key = d.get("api_key", "")
        if not api_key:
            return jsonify({"error": "Clé API requise"}), 400
        db.add_account(aid, name, "api_key", api_key=api_key)
    else:
        # OAuth: create account, tokens captured later
        access_token = d.get("access_token", "")
        refresh_token = d.get("refresh_token", "")
        expires_at = d.get("expires_at", 0)
        db.add_account(aid, name, "oauth",
                       access_token=access_token,
                       refresh_token=refresh_token,
                       expires_at=expires_at)

    return jsonify({"id": aid, "name": name}), 201


@app.route("/api/accounts/<aid>", methods=["PUT"])
def api_update(aid):
    d = request.json or {}
    try:
        kwargs = {}
        if "name" in d: kwargs["name"] = d["name"]
        if "auth_type" in d: kwargs["auth_type"] = d["auth_type"]
        if "api_key" in d: kwargs["api_key"] = d["api_key"]
        if "access_token" in d: kwargs["access_token"] = d["access_token"]
        if "refresh_token" in d: kwargs["refresh_token"] = d["refresh_token"]
        if "expires_at" in d: kwargs["expires_at"] = d["expires_at"]
        db.update_account(aid, **kwargs)
        return jsonify({"status": "updated"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


@app.route("/api/accounts/<aid>", methods=["DELETE"])
def api_delete(aid):
    db.delete_account(aid)
    return jsonify({"status": "deleted"})


@app.route("/api/accounts/<aid>/status", methods=["GET"])
def api_token_status(aid):
    return jsonify(db.get_token_status(aid))


@app.route("/api/accounts/<aid>/capture-oauth", methods=["POST"])
def api_capture_oauth(aid):
    """
    Capture OAuth tokens from ~/.claude/.credentials.json into SQLite.
    Call this after the user does `claude auth login`.
    """
    d = request.json or {}
    cred_path = d.get("credentials_path")
    try:
        result = db.capture_oauth_tokens(aid, cred_path)
        return jsonify(result)
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/accounts/<aid>/refresh-oauth", methods=["POST"])
def api_refresh_oauth(aid):
    """
    Refresh OAuth token using stored refresh token.
    Calls OAuth endpoint, updates SQLite + ~/.claude/.credentials.json.
    """
    try:
        result = db.refresh_oauth_token(aid)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except ConnectionError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        return jsonify({"error": f"Refresh failed: {e}"}), 500


@app.route("/api/accounts/<aid>/launch", methods=["POST"])
def api_launch(aid):
    """Get the launch command with injected env vars."""
    try:
        env_vars = db.get_launch_env(aid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    acc = db.get_account(aid)
    parts = [f'{k}={shlex.quote(v)}' for k, v in env_vars.items()]
    parts.append("claude")

    return jsonify({
        "command": " ".join(parts),
        "alias": f"alias claude-{acc['name']}='{' '.join(parts)}'",
        "env_keys": list(env_vars.keys()),
        "account": acc["name"],
    })


def _cli_paths():
    """Return (python_path, cli_path) using venv if available."""
    project_dir = Path(__file__).resolve().parent
    venv_python = project_dir / "venv" / "bin" / "python"
    cli_path = project_dir / "cli.py"
    py = str(venv_python) if venv_python.exists() else "python3"
    return py, str(cli_path)


@app.route("/api/generate-aliases", methods=["GET"])
def api_aliases():
    accounts = db.list_accounts()
    py, cli_path = _cli_paths()
    lines = [
        "#!/usr/bin/env bash",
        "# Claude Accounts Manager — auto-generated aliases",
        "# All accounts share one .claude dir, credentials injected via env vars",
        "",
        "# CLI wrapper (auto-activates venv)",
        "claude-accounts() {",
        f'    "{py}" "{cli_path}" "$@"',
        "}",
        "",
    ]
    for acc in accounts:
        try:
            env_vars = db.get_launch_env(acc["id"])
            parts = [f'{k}={shlex.quote(v)}' for k, v in env_vars.items()]
            parts.append("claude")
            lines.append(f"# {acc['name']} ({acc['auth_type']})")
            lines.append(f"alias claude-{acc['name']}='{' '.join(parts)}'")
            lines.append("")
        except Exception as e:
            lines.append(f"# {acc['name']} — ERROR: {e}")
            lines.append("")

    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/api/install-aliases", methods=["POST"])
def api_install():
    accounts = db.list_accounts()
    aliases_dir = Path.home() / ".claude-accounts"
    aliases_dir.mkdir(parents=True, exist_ok=True)
    aliases_file = aliases_dir / "aliases.sh"

    py, cli_path = _cli_paths()
    lines = [
        "#!/usr/bin/env bash",
        "# Claude Accounts Manager",
        "",
        "# CLI wrapper (auto-activates venv)",
        "claude-accounts() {",
        f'    "{py}" "{cli_path}" "$@"',
        "}",
        "",
    ]
    for acc in accounts:
        try:
            env_vars = db.get_launch_env(acc["id"])
            parts = [f'{k}={shlex.quote(v)}' for k, v in env_vars.items()]
            parts.append("claude")
            lines.append(f"alias claude-{acc['name']}='{' '.join(parts)}'")
        except Exception:
            pass

    aliases_file.write_text("\n".join(lines) + "\n")
    os.chmod(str(aliases_file), 0o600)

    source_line = f'source "{aliases_file}"'
    added_to = []
    for rc in [".bashrc", ".zshrc"]:
        rc_path = Path.home() / rc
        if rc_path.exists() and source_line not in rc_path.read_text():
            with open(rc_path, "a") as f:
                f.write(f"\n# Claude Accounts Manager\n{source_line}\n")
            added_to.append(rc)

    return jsonify({"aliases_file": str(aliases_file), "added_to": added_to, "count": len(accounts)})


@app.route("/api/export", methods=["GET"])
def api_export():
    return jsonify(db.export_all())


@app.route("/api/import", methods=["POST"])
def api_import():
    data = request.json
    if not isinstance(data, list):
        return jsonify({"error": "Expected JSON array"}), 400
    count = db.import_accounts(data)
    return jsonify({"imported": count})


# ── Directory browse ──────────────────────────────────────────────────────

@app.route("/api/browse", methods=["GET"])
def api_browse():
    """List directories for the directory picker. ?path= to browse."""
    raw = request.args.get("path", "")
    base = os.path.expanduser(raw) if raw else str(Path.home())
    base = os.path.abspath(base)

    if not os.path.isdir(base):
        return jsonify({"error": f"Not a directory: {base}"}), 400

    dirs = []
    try:
        for entry in sorted(os.scandir(base), key=lambda e: e.name.lower()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                has_children = False
                try:
                    has_children = any(
                        e.is_dir(follow_symlinks=False)
                        for e in os.scandir(entry.path)
                        if not e.name.startswith(".")
                    )
                except PermissionError:
                    pass
                dirs.append({
                    "name": entry.name,
                    "path": entry.path,
                    "has_children": has_children,
                })
    except PermissionError:
        return jsonify({"error": f"Permission denied: {base}"}), 403

    # Detect parent directory
    parent = os.path.dirname(base) if base != "/" else None

    return jsonify({
        "current": base,
        "parent": parent,
        "dirs": dirs,
    })


@app.route("/api/recent-dirs", methods=["GET"])
def api_recent_dirs():
    """Return common project directories (home, Projets, Desktop, etc.)."""
    home = str(Path.home())
    candidates = [
        home,
        os.path.join(home, "Projets"),
        os.path.join(home, "Projects"),
        os.path.join(home, "projects"),
        os.path.join(home, "Bureau"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents"),
        os.path.join(home, "dev"),
        os.path.join(home, "src"),
        os.path.join(home, "work"),
    ]
    shortcuts = []
    for p in candidates:
        if os.path.isdir(p):
            shortcuts.append({"name": os.path.basename(p) or "~", "path": p})

    # Find git repos in common locations (1 level deep)
    git_dirs = []
    for base in [os.path.join(home, d) for d in ["Projets", "Projects", "projects", "dev", "src", "work"]]:
        if os.path.isdir(base):
            for entry in os.scandir(base):
                if entry.is_dir(follow_symlinks=False):
                    if os.path.isdir(os.path.join(entry.path, ".git")):
                        git_dirs.append({"name": f"{os.path.basename(base)}/{entry.name}", "path": entry.path})

    return jsonify({"shortcuts": shortcuts, "projects": git_dirs[:20]})


# ── Terminal WebSocket ────────────────────────────────────────────────────

def _cleanup_terminal(sid):
    term = _terminals.pop(sid, None)
    if not term:
        return
    try:
        os.close(term["fd"])
    except OSError:
        pass
    try:
        os.kill(term["pid"], signal.SIGTERM)
    except OSError:
        pass
    try:
        os.waitpid(term["pid"], os.WNOHANG)
    except (OSError, ChildProcessError):
        pass


@socketio.on("connect")
def handle_ws_connect():
    """Reject unauthenticated WebSocket connections."""
    if not session.get("authenticated"):
        # Check API token in query string
        token = request.args.get("token", "")
        if not token or not db.verify_api_token(token):
            disconnect()
            return False


@socketio.on("start_terminal")
def handle_start_terminal(data):
    from flask import request as freq
    sid = freq.sid
    _cleanup_terminal(sid)

    account_id = data.get("account_id", "")
    try:
        env_vars = db.get_launch_env(account_id)
    except ValueError as e:
        emit("terminal_error", {"error": str(e)})
        return

    env = os.environ.copy()
    env.update(env_vars)
    if "ANTHROPIC_API_KEY" in env_vars:
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    if "CLAUDE_CODE_OAUTH_TOKEN" in env_vars:
        env.pop("ANTHROPIC_API_KEY", None)
    env.setdefault("TERM", "xterm-256color")

    # Working directory
    cwd = data.get("cwd", "") or str(Path.home())
    if not os.path.isdir(cwd):
        cwd = str(Path.home())

    pid, fd = pty.fork()
    if pid == 0:
        # Child — change directory then exec claude
        os.chdir(cwd)
        os.execvpe("claude", ["claude"], env)
    else:
        # Parent — track and relay
        _terminals[sid] = {"fd": fd, "pid": pid}
        emit("terminal_started")

        def _read_loop():
            while sid in _terminals:
                try:
                    r, _, _ = select.select([fd], [], [], 0.1)
                    if r:
                        chunk = os.read(fd, 4096)
                        if chunk:
                            socketio.emit(
                                "terminal_output",
                                {"data": chunk.decode("utf-8", errors="replace")},
                                room=sid,
                            )
                        else:
                            break
                except (OSError, IOError):
                    break
            socketio.emit("terminal_exit", room=sid)

        t = threading.Thread(target=_read_loop, daemon=True)
        t.start()


@socketio.on("start_login")
def handle_start_login(data):
    """
    Launch 'claude' without credentials so OAuth login flow triggers.
    Monitor ~/.claude/.credentials.json and auto-capture tokens once
    the user completes authentication.
    """
    from flask import request as freq
    import time as _time

    sid = freq.sid
    _cleanup_terminal(sid)

    account_id = data.get("account_id", "")
    acc = db.get_account(account_id)
    if not acc:
        emit("terminal_error", {"error": f"Compte {account_id} introuvable"})
        return

    # Clean env — strip any existing Claude credentials so auth flow triggers
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    env.setdefault("TERM", "xterm-256color")

    # Working directory
    cwd = data.get("cwd", "") or str(Path.home())
    if not os.path.isdir(cwd):
        cwd = str(Path.home())

    # Snapshot the credentials file mtime before launching
    cred_path = Path.home() / ".claude" / ".credentials.json"
    initial_mtime = cred_path.stat().st_mtime if cred_path.exists() else 0

    pid, fd = pty.fork()
    if pid == 0:
        # Child — change directory then exec claude (no credentials → forces auth)
        os.chdir(cwd)
        os.execvpe("claude", ["claude"], env)
    else:
        _terminals[sid] = {"fd": fd, "pid": pid, "login_account": account_id}
        emit("terminal_started")

        def _read_loop():
            while sid in _terminals:
                try:
                    r, _, _ = select.select([fd], [], [], 0.1)
                    if r:
                        chunk = os.read(fd, 4096)
                        if chunk:
                            socketio.emit(
                                "terminal_output",
                                {"data": chunk.decode("utf-8", errors="replace")},
                                room=sid,
                            )
                        else:
                            break
                except (OSError, IOError):
                    break
            socketio.emit("terminal_exit", room=sid)

        def _watch_credentials():
            """Poll .credentials.json for new/updated tokens."""
            mtime_ref = initial_mtime
            while sid in _terminals:
                try:
                    if cred_path.exists():
                        cur = cred_path.stat().st_mtime
                        if cur > mtime_ref:
                            _time.sleep(0.5)  # let claude finish writing
                            try:
                                result = db.capture_oauth_tokens(account_id)
                                socketio.emit("login_complete", {
                                    "account_id": account_id,
                                    "token_preview": result["token_preview"],
                                    "has_refresh": result["has_refresh"],
                                    "expires_in_min": result["expires_in_min"],
                                }, room=sid)
                                return  # tokens captured
                            except Exception:
                                mtime_ref = cur
                except (OSError, IOError):
                    pass
                _time.sleep(2)

        t1 = threading.Thread(target=_read_loop, daemon=True)
        t2 = threading.Thread(target=_watch_credentials, daemon=True)
        t1.start()
        t2.start()


@socketio.on("terminal_input")
def handle_terminal_input(data):
    from flask import request as freq
    term = _terminals.get(freq.sid)
    if term:
        try:
            os.write(term["fd"], data["data"].encode("utf-8"))
        except (OSError, IOError):
            pass


@socketio.on("resize_terminal")
def handle_resize(data):
    from flask import request as freq
    term = _terminals.get(freq.sid)
    if term:
        try:
            rows = int(data.get("rows", 24))
            cols = int(data.get("cols", 80))
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(term["fd"], termios.TIOCSWINSZ, winsize)
        except (OSError, IOError, ValueError):
            pass


@socketio.on("stop_terminal")
def handle_stop():
    from flask import request as freq
    _cleanup_terminal(freq.sid)


@socketio.on("disconnect")
def handle_disconnect():
    from flask import request as freq
    _cleanup_terminal(freq.sid)


# ── Serve frontend ──

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


def start_server(host="127.0.0.1", port=5111, open_browser=True):
    db.init_db()
    url = f"http://{'localhost' if host == '127.0.0.1' else host}:{port}"
    print(f"\n  Claude Accounts Manager")
    print(f"  -> {url}")
    print(f"  -> DB: {db.DB_PATH}")
    if host != "127.0.0.1":
        print(f"  -> WARNING: Listening on {host} — use HTTPS/SSH tunnel for remote access")
    print(f"  -> Auth: {'configured' if db.is_setup_done() else 'setup required (first visit)'}")
    print()

    if open_browser and not os.environ.get("DOCKER_CONTAINER"):
        def _open():
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", action="store_true", help="Bind to 0.0.0.0")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    parser.add_argument("--port", "-p", type=int, default=int(os.environ.get("PORT", 5111)))
    args = parser.parse_args()
    host = "0.0.0.0" if args.remote else "127.0.0.1"
    start_server(host=host, port=args.port, open_browser=not args.no_browser)
