#!/usr/bin/env python3
"""
claude-accounts CLI — Manage and launch Claude with multiple accounts.
Single shared .claude dir. Credentials injected via env vars from SQLite.

Usage:
    claude-accounts add <name> --key <api_key>      Add API key account
    claude-accounts add <name> --oauth               Add OAuth account (empty)
    claude-accounts login <name>                     Login + capture OAuth tokens
    claude-accounts refresh <name>                   Refresh OAuth token
    claude-accounts list                             List all accounts
    claude-accounts launch <name> [-- args...]       Launch claude with account
    claude-accounts remove <name>                    Remove account
    claude-accounts status <name>                    Check token status
    claude-accounts aliases                          Print generated aliases
    claude-accounts install                          Install aliases in shell
    claude-accounts export                           Export accounts JSON
    claude-accounts import <file>                    Import accounts JSON
    claude-accounts serve [--port 5111]              Start web UI
"""

import sys
import os
import json
import shlex
import argparse
import subprocess
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db


def cmd_add(args):
    name = args.name.lower().strip().replace(" ", "-")
    if len(name) < 2:
        print("✗ Nom trop court (min 2 car.)")
        sys.exit(1)
    if db.get_account_by_name(name):
        print(f"✗ '{name}' existe déjà")
        sys.exit(1)

    aid = f"acc_{name}_{os.urandom(4).hex()}"

    if args.oauth:
        db.add_account(aid, name, "oauth")
        print(f"✓ Compte OAuth '{name}' créé")
        print(f"\n  Prochaine étape — capturer les tokens :")
        print(f"  claude-accounts login {name}")
    else:
        api_key = args.key
        if not api_key:
            import getpass
            api_key = getpass.getpass("Clé API (sk-ant-...): ")
        if not api_key:
            print("✗ Clé API requise")
            sys.exit(1)
        db.add_account(aid, name, "api_key", api_key=api_key)
        print(f"✓ Compte '{name}' ajouté (API Key)")

    print(f"  Alias : claude-{name}")


def cmd_login(args):
    """
    OAuth login flow:
    1. User authenticates by running `claude` (auth happens automatically on first launch)
    2. This command captures tokens from ~/.claude/.credentials.json → SQLite
    """
    name = args.name.lower().strip()
    acc = db.get_account_by_name(name)

    if not acc:
        # Auto-create OAuth account if it doesn't exist
        aid = f"acc_{name}_{os.urandom(4).hex()}"
        db.add_account(aid, name, "oauth")
        acc = db.get_account_by_name(name)
        print(f"  Compte OAuth '{name}' créé automatiquement")

    if acc["auth_type"] != "oauth":
        print(f"✗ '{name}' est un compte API key, pas OAuth")
        sys.exit(1)

    # Check if credentials file exists
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if not cred_path.exists():
        print(f"\n✗ Fichier {cred_path} introuvable.")
        print(f"  Lance d'abord : claude")
        print(f"  L'authentification se fait automatiquement au premier lancement.")
        print(f"  Ensuite relance : claude-accounts login {name}")
        sys.exit(1)

    # Capture tokens from existing credentials
    print(f"\n  ⏳ Capture des tokens pour '{name}'...")
    try:
        info = db.capture_oauth_tokens(acc["id"])
        print(f"  ✓ Token capturé : {info['token_preview']}")
        if info["has_refresh"]:
            print(f"  ✓ Refresh token : présent")
        if info["expires_in_min"] is not None:
            hours = info["expires_in_min"] // 60
            print(f"  ⏰ Expire dans : ~{hours}h")
        print(f"\n  Utilise maintenant : claude-{name}")
    except Exception as e:
        print(f"\n✗ Erreur capture : {e}")
        sys.exit(1)


def cmd_list(args):
    accounts = db.list_accounts()
    if not accounts:
        print("Aucun compte. Utilise : claude-accounts add <nom>")
        return

    print(f"\n  {'Alias':<20} {'Type':<8} {'Credential':<22} {'Status':<10} {'Dernier usage'}")
    print("  " + "─" * 78)

    for acc in accounts:
        alias = f"claude-{acc['name']}"
        cred = acc["masked_key"] or "(vide)"
        last = acc["last_used"] or "—"

        if acc["auth_type"] == "oauth":
            status = acc.get("token_status", "?")
            if status == "valid":
                mins = acc.get("expires_in_min", 0)
                status = f"✓ {mins // 60}h" if mins else "✓"
            elif status == "expired":
                status = "⚠ expiré"
            elif not acc["masked_key"]:
                status = "⚡ login"
        else:
            status = "✓" if acc["masked_key"] else "✗"

        print(f"  {alias:<20} {acc['auth_type']:<8} {cred:<22} {status:<10} {last}")

    print()


def cmd_status(args):
    acc = db.get_account_by_name(args.name)
    if not acc:
        print(f"✗ '{args.name}' introuvable")
        sys.exit(1)

    status = db.get_token_status(acc["id"])
    print(f"\n  Compte : {args.name} ({acc['auth_type']})")
    print(f"  Status : {status['status']}")

    if status.get("remaining_min") is not None:
        h, m = divmod(status["remaining_min"], 60)
        print(f"  Expire dans : {h}h{m:02d}m")
    if status.get("has_refresh"):
        print(f"  Refresh token : présent")
    print()


def cmd_refresh(args):
    """Refresh an OAuth token using the stored refresh token."""
    name = args.name.lower().strip()
    acc = db.get_account_by_name(name)
    if not acc:
        print(f"✗ '{name}' introuvable")
        sys.exit(1)
    if acc["auth_type"] != "oauth":
        print(f"✗ '{name}' est un compte API key, pas OAuth")
        sys.exit(1)

    print(f"\n  ⏳ Refresh du token pour '{name}'...")
    try:
        result = db.refresh_oauth_token(acc["id"])
        print(f"  ✓ Token rafraîchi : {result['token_preview']}")
        if result["expires_in_min"] is not None:
            hours = result["expires_in_min"] // 60
            mins = result["expires_in_min"] % 60
            print(f"  ⏰ Expire dans : {hours}h{mins:02d}m")
        print(f"\n  Utilise maintenant : claude-{name}")
    except Exception as e:
        print(f"\n✗ Erreur refresh : {e}")
        sys.exit(1)


def cmd_launch(args):
    acc = db.get_account_by_name(args.name)
    if not acc:
        print(f"✗ '{args.name}' introuvable")
        sys.exit(1)

    try:
        env_vars = db.get_launch_env(acc["id"])
    except ValueError as e:
        print(f"✗ {e}")
        sys.exit(1)

    env = os.environ.copy()
    env.update(env_vars)

    # Remove conflicting env vars
    if "ANTHROPIC_API_KEY" in env_vars:
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    if "CLAUDE_CODE_OAUTH_TOKEN" in env_vars:
        env.pop("ANTHROPIC_API_KEY", None)

    claude_args = args.claude_args or []
    cmd = ["claude"] + claude_args

    print(f"  ▶ Claude → {args.name} ({list(env_vars.keys())[0]})")
    os.execvpe("claude", cmd, env)


def cmd_remove(args):
    acc = db.get_account_by_name(args.name)
    if not acc:
        print(f"✗ '{args.name}' introuvable")
        sys.exit(1)

    confirm = input(f"  Supprimer '{args.name}' ? [y/N] ").strip().lower()
    if confirm == "y":
        db.delete_account(acc["id"])
        print(f"  ✓ '{args.name}' supprimé")
    else:
        print("  Annulé")


def _cli_paths():
    """Return (python_path, cli_path) using venv if available."""
    project_dir = Path(__file__).resolve().parent
    venv_python = project_dir / "venv" / "bin" / "python"
    cli_path = project_dir / "cli.py"
    py = str(venv_python) if venv_python.exists() else "python3"
    return py, str(cli_path)


def cmd_aliases(args):
    accounts = db.list_accounts()
    if not accounts:
        print("# Aucun compte")
        return

    py, cli_path = _cli_paths()

    print("#!/usr/bin/env bash")
    print("# Claude Accounts — one shared .claude, injected env vars")
    print()
    print("# CLI wrapper (auto-activates venv)")
    print("claude-accounts() {")
    print(f'    "{py}" "{cli_path}" "$@"')
    print("}")
    print()

    for acc in accounts:
        try:
            env_vars = db.get_launch_env(acc["id"])
            parts = [f'{k}={shlex.quote(v)}' for k, v in env_vars.items()]
            parts.append("claude")
            print(f"alias claude-{acc['name']}='{' '.join(parts)}'")
        except Exception as e:
            print(f"# {acc['name']}: {e}")


def cmd_install(args):
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
    print(f"  ✓ Aliases → {aliases_file}")

    source_line = f'source "{aliases_file}"'
    for rc in [".bashrc", ".zshrc"]:
        rc_path = Path.home() / rc
        if rc_path.exists() and source_line not in rc_path.read_text():
            with open(rc_path, "a") as f:
                f.write(f"\n# Claude Accounts Manager\n{source_line}\n")
            print(f"  ✓ Source ajouté dans ~/{rc}")

    print(f"\n  Relance ton shell ou : source {aliases_file}")


def cmd_export(args):
    print(json.dumps(db.export_all(), indent=2))


def cmd_import(args):
    with open(args.file) as f:
        data = json.load(f)
    count = db.import_accounts(data)
    print(f"  ✓ {count} compte(s) importé(s)")


def cmd_serve(args):
    port = args.port or 5111
    os.environ["PORT"] = str(port)
    from server import socketio as sio, app as flask_app
    db.init_db()
    sio.run(flask_app, host="127.0.0.1", port=port, debug=True, allow_unsafe_werkzeug=True)


def main():
    db.init_db()

    p = argparse.ArgumentParser(prog="claude-accounts",
                                 description="Multi-comptes Claude Code — un seul .claude, credentials dans SQLite")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("add", help="Ajouter un compte")
    s.add_argument("name")
    s.add_argument("--key", "-k", help="Clé API")
    s.add_argument("--oauth", action="store_true")

    s = sub.add_parser("login", help="Login OAuth + capture tokens")
    s.add_argument("name")

    sub.add_parser("list", aliases=["ls"])

    s = sub.add_parser("status", help="Status d'un compte")
    s.add_argument("name")

    s = sub.add_parser("refresh", help="Rafraîchir un token OAuth")
    s.add_argument("name")

    s = sub.add_parser("launch", aliases=["run"], help="Lancer claude avec un compte")
    s.add_argument("name")
    s.add_argument("claude_args", nargs="*")

    s = sub.add_parser("remove", aliases=["rm"])
    s.add_argument("name")

    sub.add_parser("aliases")
    sub.add_parser("install")
    sub.add_parser("export")

    s = sub.add_parser("import")
    s.add_argument("file")

    s = sub.add_parser("serve", help="Lancer l'interface web")
    s.add_argument("--port", "-p", type=int, default=5111)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    cmds = {
        "add": cmd_add, "login": cmd_login,
        "list": cmd_list, "ls": cmd_list,
        "status": cmd_status, "refresh": cmd_refresh,
        "launch": cmd_launch, "run": cmd_launch,
        "remove": cmd_remove, "rm": cmd_remove,
        "aliases": cmd_aliases, "install": cmd_install,
        "export": cmd_export, "import": cmd_import,
        "serve": cmd_serve,
    }

    fn = cmds.get(args.cmd)
    if fn:
        fn(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
