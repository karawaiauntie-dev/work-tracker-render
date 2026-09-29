"""Local owner control center for isolated Work Tracker clients.

Place beside create_client_team.py in the project root, then run:
    python owner_portal.py
The first run prints a one-time owner password in your own terminal.
Bind address is 127.0.0.1; do not expose this development server publicly.
"""

import hmac
import os
import re
import secrets
import socket
import sqlite3
import subprocess
from pathlib import Path

import requests
from dotenv import dotenv_values
from flask import Flask, abort, redirect, render_template_string, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from create_client_team import create_client_team
from bot_health import read_status

ROOT = Path(__file__).resolve().parent
TEAMS = ROOT / "teams"
OWNER_ENV = ROOT / ".owner.env"
SLUG = re.compile(r"[a-z][a-z0-9_]{2,39}\Z")
USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,39}\Z")


def write_private(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def owner_settings():
    if not OWNER_ENV.exists():
        password = secrets.token_urlsafe(24)
        write_private(OWNER_ENV, "OWNER_USERNAME=owner\nOWNER_PASSWORD_HASH=" +
                      generate_password_hash(password) + "\nOWNER_SECRET=" + secrets.token_hex(32) + "\n")
        print("Owner username: owner")
        print("One-time owner password:", password)
        print("Save it privately; it will not be printed again.")
    settings = dotenv_values(OWNER_ENV)
    return settings


settings = owner_settings()
app = Flask(__name__)
app.secret_key = settings["OWNER_SECRET"]
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


BASE_STYLE = """
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f7f3;color:#163c40;font:14px Arial,sans-serif}
header{background:#083b45;color:white;padding:20px max(24px,5vw);display:flex;align-items:center;justify-content:space-between}
header strong{font:700 23px Georgia,serif}header a{color:#def7ed}main{max-width:1300px;margin:auto;padding:30px 24px}
h1{font:700 37px Georgia,serif;margin:0 0 8px}.muted{color:#6c8587}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.card{background:white;border:1px solid #e5ede6;border-radius:18px;padding:21px;box-shadow:0 8px 24px #1836330b}
.hero{background:linear-gradient(115deg,#083b45,#0a8b77);color:#fff;border-radius:22px;padding:28px 32px;margin-bottom:20px}.hero h1{color:#fff}.hero p{color:#d7f0e9}
form{margin:0}label{display:block;margin:11px 0 5px;font-size:12px;font-weight:bold;color:#536e70}
input{width:100%;padding:11px;border:1px solid #cadbd5;border-radius:9px;font:inherit}
button{cursor:pointer;border:0;border-radius:9px;background:#087963;color:white;padding:11px 15px;font-weight:bold;margin-top:12px}
button.secondary{background:#e4f2ea;color:#0a6657}button.warn{background:#ae5a22}button.danger{background:#a53f3a}
.row{display:flex;gap:9px;flex-wrap:wrap}.row>*{flex:1}.meta{line-height:1.8;color:#526b6e}.meta strong{color:#1b494a}
.notice{padding:13px 16px;margin:0 0 16px;border-radius:10px;background:#e3f3e9;color:#0b6349}.notice.error{background:#ffe9e6;color:#a43231}
.tag{display:inline-block;padding:5px 9px;border-radius:20px;font-size:11px;font-weight:bold;background:#e1f3e8;color:#126b53}.tag.off{background:#fce8e3;color:#a44237}
summary{cursor:pointer;color:#0b7165;font-weight:bold;margin-top:15px}details{border-top:1px solid #edf1ec;margin-top:15px;padding-top:3px}
@media(max-width:650px){header{padding:17px}main{padding:20px 14px}.hero{padding:24px}.row{display:block}}
</style>
"""

LOGIN = BASE_STYLE + """
<main style="max-width:440px;padding-top:11vh"><section class="card"><h1>Owner login</h1>
<p class="muted">Manage your Work Tracker clients.</p>
{% if error %}<div class="notice error">{{ error }}</div>{% endif %}
<form method="post"><label>Username</label><input name="username" required autofocus>
<label>Password</label><input name="password" type="password" required>
<button type="submit" style="width:100%">Sign in</button></form></section></main>
"""

DASHBOARD = BASE_STYLE + """
<header><strong>✦ Work Tracker Owner</strong><a href="{{ url_for('logout') }}">Log out</a></header>
<main><section class="hero"><h1>Client control center</h1><p>Create accounts, verify bots, and monitor each team's requests.</p></section>
{% if message %}<div class="notice {{ 'error' if error else '' }}">{{ message }}</div>{% endif %}
{% if credentials %}<div class="notice"><strong>New login — copy this now:</strong> Username {{ credentials.username }} · Password <code>{{ credentials.password }}</code><br>This password will not be shown again on this page.</div>{% endif %}
<section class="card" style="margin-bottom:20px"><h2>Create client</h2>
<form method="post" action="{{ url_for('create_client') }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<div class="row"><div><label>Folder slug</label><input name="slug" placeholder="client_two" pattern="[a-z][a-z0-9_]{2,39}" required></div>
<div><label>Team name</label><input name="team_name" placeholder="Client Two" maxlength="60" required></div>
<div><label>Client username</label><input name="username" placeholder="client_two_admin" required></div>
<div><label>Local port</label><input name="port" type="number" min="1024" max="65535" value="{{ suggested_port }}" required></div></div>
<button type="submit">Create client account</button></form></section>
<h2>Clients · {{ clients|length }}</h2><div class="grid">
{% for client in clients %}<section class="card"><h2 style="margin:0 0 8px">{{ client.name }}</h2>
<span class="tag {{ 'off' if not client.active else '' }}">{{ 'Active' if client.active else 'Paused' }}</span>
<div class="meta" style="margin-top:12px"><strong>Username:</strong> {{ client.username }}<br>
<strong>Dashboard:</strong> {% if client.domain %}<a href="https://{{ client.domain }}/" target="_blank" rel="noopener">https://{{ client.domain }}/</a>{% else %}Local port {{ client.port }} · no public domain{% endif %}<br>
<strong>Bot:</strong> {{ client.bot_name or ('Token saved' if client.token_set else 'Token missing') }}<br>
<strong>Workers:</strong> {{ client.workers }} · <strong>Pending requests:</strong> {{ client.pending }}<br>
<strong>Bot status:</strong> {{ client.bot_status.label }} — {{ client.bot_status.detail }}<br>Last checked: {{ client.bot_status.checked or "Not yet" }}<br>
<strong>Services:</strong> {{ client.services }}</div>
<details><summary>Manage account and bot</summary>
<form method="post" action="{{ url_for('set_token', slug=client.slug) }}"><input type="hidden" name="csrf" value="{{ csrf }}"><label>New bot token</label><input name="token" type="password" autocomplete="off" required><button type="submit">Verify and save bot token</button></form>
<form method="post" action="{{ url_for('reset_password', slug=client.slug) }}" onsubmit="return confirm('Reset this client password?')"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="secondary" type="submit">Reset password</button></form>
<form method="post" action="{{ url_for('toggle_client', slug=client.slug) }}"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="{{ 'danger' if client.active else 'secondary' }}" type="submit">{{ 'Pause client login' if client.active else 'Resume client login' }}</button></form>
</details></section>{% else %}<div class="card muted">No clients yet. Create the first one above.</div>{% endfor %}
</div></main>
"""


def config_for(slug):
    if not SLUG.fullmatch(slug):
        abort(404)
    folder = TEAMS / slug
    path = folder / ".env"
    if not path.is_file():
        abort(404)
    return folder, path, dotenv_values(path)


def update_config(path, key, value):
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    write_private(path, "\n".join(lines) + "\n")


def verify_post():
    if not session.get("owner") or not hmac.compare_digest(request.form.get("csrf", ""), session.get("csrf", "missing")):
        abort(403)


def readable_counts(folder, config):
    db = Path(config.get("DB_PATH") or "worker_bot.db")
    if not db.is_absolute():
        db = folder / db
    if not db.is_file():
        return 0, 0
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2) as conn:
            workers = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            pending = sum(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ({statuses})").fetchone()[0]
                          for table, statuses in (("agent_invites", "'pending'"),
                                                  ("agent_overview", "'pending'"),
                                                  ("withdrawals", "'verification','pending','payment_pending','approved'")))
            return workers, pending
    except sqlite3.Error:
        return 0, 0


def service_status(folder):
    if not (folder / "compose.yaml").is_file():
        port = int((dotenv_values(folder / ".env").get("FLASK_PORT") or "0"))
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return "Local dashboard reachable · bot not monitored"
        except (OSError, ValueError):
            return "Local dashboard offline · bot not monitored"
    try:
        result = subprocess.run(["docker", "compose", "--project-directory", str(folder), "ps", "--format", "json"],
                                capture_output=True, text=True, timeout=4)
        if result.returncode == 0 and result.stdout.strip():
            import json
            records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            return ", ".join(f"{item.get('Service')}: {item.get('State')}" for item in records) or "No services started"
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return "Server status unavailable from this computer"


def client_rows():
    rows = []
    for folder in sorted(TEAMS.iterdir()) if TEAMS.exists() else []:
        if not folder.is_dir() or not SLUG.fullmatch(folder.name) or not (folder / ".env").is_file():
            continue
        cfg = dotenv_values(folder / ".env")
        workers, pending = readable_counts(folder, cfg)
        rows.append(dict(slug=folder.name, name=cfg.get("TEAM_NAME") or folder.name,
                         username=cfg.get("ADMIN_USERNAME") or "admin", domain=cfg.get("CLIENT_DOMAIN") or "",
                         port=cfg.get("FLASK_PORT") or "?", token_set=bool(cfg.get("TELEGRAM_BOT_TOKEN")),
                         bot_name=cfg.get("BOT_USERNAME") or "", active=cfg.get("CLIENT_ACTIVE", "1") != "0",
                         workers=workers, pending=pending, services=service_status(folder), bot_status=read_status(folder, cfg)))
    return rows


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (hmac.compare_digest(request.form.get("username", ""), settings["OWNER_USERNAME"]) and
                check_password_hash(settings["OWNER_PASSWORD_HASH"], request.form.get("password", ""))):
            session.clear()
            session["owner"] = True
            session["csrf"] = secrets.token_urlsafe(24)
            return redirect(url_for("index"))
        error = "Invalid owner login"
    return render_template_string(LOGIN, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    if not session.get("owner"):
        return redirect(url_for("login"))
    rows = client_rows()
    ports = [int(row["port"]) for row in rows if str(row["port"]).isdigit()]
    return render_template_string(DASHBOARD, clients=rows, csrf=session["csrf"],
                                  suggested_port=max([5000] + ports) + 1,
                                  message=session.pop("message", None), error=session.pop("error", False),
                                  credentials=session.pop("credentials", None))


def respond(message, error=False, credentials=None):
    session["message"] = message
    session["error"] = error
    if credentials:
        session["credentials"] = credentials
    return redirect(url_for("index"))


@app.post("/clients")
def create_client():
    verify_post()
    slug = request.form.get("slug", "")
    team_name = request.form.get("team_name", "")
    username = request.form.get("username", "")
    try:
        port = int(request.form.get("port", ""))
        if port in [int(row["port"]) for row in client_rows() if str(row["port"]).isdigit()]:
            raise ValueError("Port already used by another client")
        folder = create_client_team(ROOT, slug, team_name, port, username=username)
        cfg = dotenv_values(folder / ".env")
        return respond("Client created. Add its unique bot token before starting the bot.",
                       credentials=dict(username=username, password=cfg["ADMIN_PASSWORD"]))
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError) as exc:
        return respond(str(exc), error=True)


@app.post("/clients/<slug>/token")
def set_token(slug):
    verify_post()
    folder, path, _ = config_for(slug)
    token = request.form.get("token", "").strip()
    if not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{20,100}", token):
        return respond("Token format is invalid.", error=True)
    for other in TEAMS.iterdir():
        if other != folder and (other / ".env").is_file() and dotenv_values(other / ".env").get("TELEGRAM_BOT_TOKEN") == token:
            return respond("This bot token is already assigned to another client.", error=True)
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/getMe", timeout=8)
        result = response.json()
        if not response.ok or not result.get("ok") or not result.get("result", {}).get("is_bot"):
            return respond("Telegram did not accept this bot token.", error=True)
        bot_username = result["result"].get("username", "")
    except (requests.RequestException, ValueError, KeyError):
        return respond("Could not verify the bot token with Telegram. Try again.", error=True)
    update_config(path, "TELEGRAM_BOT_TOKEN", token)
    update_config(path, "BOT_USERNAME", bot_username)
    return respond(f"Verified @{bot_username}. Restart this client's bot and dashboard to load the new token.")


@app.post("/clients/<slug>/password")
def reset_password(slug):
    verify_post()
    _, path, cfg = config_for(slug)
    password = secrets.token_urlsafe(20)
    update_config(path, "ADMIN_PASSWORD", password)
    update_config(path, "ADMIN_CREDENTIAL_VERSION", secrets.token_hex(16))
    return respond("Client password reset. Existing dashboard sessions will be signed out.",
                   credentials=dict(username=cfg.get("ADMIN_USERNAME") or "admin", password=password))


@app.post("/clients/<slug>/active")
def toggle_client(slug):
    verify_post()
    _, path, cfg = config_for(slug)
    active = cfg.get("CLIENT_ACTIVE", "1") != "0"
    update_config(path, "CLIENT_ACTIVE", "0" if active else "1")
    return respond("Client login paused." if active else "Client login resumed.")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5010, debug=False)
