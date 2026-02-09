"""
Claude Accounts Manager — Flask API
All credentials in SQLite, injected as env vars at launch time.
Single shared .claude dir.
"""

import os
import uuid
import shlex
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, Response

import db

app = Flask(__name__, static_folder="static")


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


# ── Serve frontend ──

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("PORT", 5111))
    print(f"\n  🔶 Claude Accounts Manager")
    print(f"  → http://localhost:{port}")
    print(f"  → DB: {db.DB_PATH}")
    print(f"  → Single shared .claude dir — credentials injected via env vars\n")
    app.run(host="127.0.0.1", port=port, debug=True)
