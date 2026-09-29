"""Provision a client-specific Work Tracker bot and admin dashboard.

Run from the project root (the directory containing admin_dashboard/):
    python create_client_team.py client_one --team-name "Client One" --port 5002 --username client_one_admin
"""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def dotenv_value(value: str) -> str:
    """Quote display values safely for python-dotenv."""
    return json.dumps(value, ensure_ascii=False)


def create_client_team(project_root: Path, slug: str, team_name: str, port: int,
                     admin_id: str = "", username: str = "demo_admin",
                     display_name: str = "") -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,39}", slug):
        raise ValueError("Team slug must be 3–40 lowercase letters, digits, or underscores, starting with a letter.")
    if not 1024 <= port <= 65535:
        raise ValueError("Use an available port between 1024 and 65535.")
    if not team_name.strip() or len(team_name) > 60 or "\n" in team_name:
        raise ValueError("Team name must be 1–60 characters on one line.")
    if admin_id and not admin_id.isdigit():
        raise ValueError("Admin Telegram ID must contain digits only.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,39}", username):
        raise ValueError("Username must be 3–40 letters, digits, or underscores, starting with a letter.")
    if display_name and (len(display_name.strip()) > 50 or "\n" in display_name or not display_name.strip()):
        raise ValueError("Admin display name must be 1–50 characters on one line.")

    source = project_root / "admin_dashboard"
    bot_source = next(
        (path for path in (project_root / "work_tracker_bot.py", source / "work_tracker_bot.py") if path.is_file()),
        None,
    )
    if not (source / "app.py").is_file() or bot_source is None:
        raise FileNotFoundError(
            "Expected admin_dashboard/app.py and work_tracker_bot.py in the project root or admin_dashboard."
        )
    if not (source / "templates").is_dir():
        raise FileNotFoundError("admin_dashboard/templates is required.")

    teams_dir = project_root / "teams"
    teams_dir.mkdir(exist_ok=True)
    target = teams_dir / slug
    if target.exists():
        raise FileExistsError(f"{target} already exists. Choose a new slug; no files were overwritten.")

    staging = teams_dir / f".{slug}_{secrets.token_hex(8)}"
    staging.mkdir()
    try:
        app_dir = staging / "admin_dashboard"
        app_dir.mkdir()
        shutil.copy2(source / "app.py", app_dir / "app.py")
        for dependency in ("worker_writes.py", "subscriptions.py", "bot_health.py"):
            shutil.copy2(source / dependency, app_dir / dependency)
        shutil.copy2(bot_source, app_dir / "work_tracker_bot.py")
        geography = next(
            (path for path in (source / "geography_questions.py", project_root / "geography_questions.py") if path.is_file()),
            None,
        )
        if geography is None:
            raise FileNotFoundError("geography_questions.py is required to run the bot.")
        shutil.copy2(geography, app_dir / "geography_questions.py")
        shutil.copytree(source / "templates", app_dir / "templates")
        static_source = source / "static"
        if static_source.is_dir():
            shutil.copytree(static_source, app_dir / "static", ignore=shutil.ignore_patterns("receipts"))
        for filename in ("requirements.txt",):
            candidate = project_root / filename
            if candidate.is_file():
                shutil.copy2(candidate, staging / filename)

        password = secrets.token_urlsafe(18)
        env_text = "\n".join([
            "# Client instance. Use a unique BotFather token and keep this file private.",
            "TELEGRAM_BOT_TOKEN=",
            f"TEAM_NAME={dotenv_value(team_name.strip())}",
            f"ADMIN_DISPLAY_NAME={dotenv_value(display_name.strip() or team_name.strip())}",
            f"ADMIN_IDS={admin_id}",
            f"ADMIN_USERNAME={username}",
            f"ADMIN_PASSWORD={password}",
            f"FLASK_SECRET_KEY={secrets.token_hex(32)}",
            "DB_PATH=worker_bot.db",
            f"CLIENT_SLUG={slug}",
            f"BILLING_DB_PATH={dotenv_value(str((project_root / 'subscriptions.db').resolve()))}",
            f"FLASK_PORT={port}",
            f"FLASK_CHAT_URL=http://127.0.0.1:{port}/worker_message",
            "FLASK_DEBUG=0",
            f"SESSION_COOKIE_NAME=worktracker_{slug}",
            "",
        ])
        env_path = staging / ".env"
        env_path.write_text(env_text, encoding="utf-8")
        try:
            env_path.chmod(0o600)
        except OSError:
            pass

        # Initialize a fresh database using the project's own schema routines.
        setup = "import dotenv; dotenv.load_dotenv=lambda *a,**k: False; import work_tracker_bot; work_tracker_bot.init_db(); import app; app.ensure_database_ready()"
        runtime_env = os.environ.copy()
        runtime_env["TELEGRAM_BOT_TOKEN"] = "000:SETUP_ONLY"
        runtime_env["DB_PATH"] = str(staging / "worker_bot.db")
        completed = subprocess.run(
            [sys.executable, "-c", setup], cwd=app_dir, env=runtime_env,
            capture_output=True, text=True, timeout=90,
        )
        if completed.returncode:
            raise RuntimeError("Client database setup failed. Install project requirements first.\n" + completed.stderr[-1800:])

        (staging / "README.txt").write_text(
            f"{team_name.strip()} — isolated client instance\n\n"
            f"Local dashboard: http://127.0.0.1:{port}/\n"
            f"Admin username: {username}\n"
            "Admin password: see .env (keep it private)\n\n"
            "From this client folder, run: python admin_dashboard/app.py\n"
            "The bot needs its own BotFather token in .env before you run python admin_dashboard/work_tracker_bot.py.\n"
            "Set ADMIN_IDS in .env to the client's Telegram admin ID for message alerts.\n"
            "This folder has a new worker_bot.db. It does not include the main team's users or receipts.\n"
            "Use a distinct bot token and available port for every team.\n"
            "For real remote access, deploy this instance behind HTTPS with a production server and persistent storage.\n"
            "Localhost is only reachable on your computer; provisioning does not publish a website.\n"
            "Local billing: My Subscription in the dashboard. The owner verifies invoices. Remote billing needs a separate authenticated service.\n",
            encoding="utf-8",
        )
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="Client folder name, for example client_one")
    parser.add_argument("--team-name", required=True, help="Name shown in the dashboard and bot")
    parser.add_argument("--port", type=int, default=5001, help="Client dashboard port (default: 5001)")
    parser.add_argument("--admin-id", default="", help="Optional Telegram ID for client admin alerts")
    parser.add_argument("--username", default="client_admin", help="Client's dashboard username")
    parser.add_argument("--display-name", default="", help="Client's display name in the dashboard")
    args = parser.parse_args()
    target = create_client_team(Path(__file__).resolve().parent, args.slug, args.team_name,
                              args.port, args.admin_id, args.username, args.display_name)
    print(f"Created {target}")
    print(f"Login: {args.username}; password is stored in the new team's .env file.")
    print(f"Run: python {target / 'admin_dashboard' / 'app.py'}")


if __name__ == "__main__":
    main()
