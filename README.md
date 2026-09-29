# Work Tracker — Clean Version

Telegram bot + Flask admin dashboard.

## Setup

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Create your config:

   ```bash
   copy .env.example .env
   ```

   Then edit `.env`:
   - `TELEGRAM_BOT_TOKEN` — 8707838965:AAEuCIfrP_1sLjaAGXJb6Vq0malNbSdq2ME
   - `ADMIN_IDS` — 7828073732
   - `ADMIN_PASSWORD` — reyrey3
   - `FLASK_SECRET_KEY` — `python -c "import secrets; print(secrets.token_hex(32))"`

3. Put the database next to this folder (name `worker_bot.db`, or set `DB_PATH` in `.env`).
   The bot and dashboard will create/migrate it automatically — existing data is kept.

## Run the bot

```bash
python work_tracker_bot.py
```

## Run the dashboard

```bash
python admin_dashboard/app.py
```

Open `http://localhost:5000` and log in with the admin credentials from `.env`.

## Security notes

- Rotate your bot token via @BotFather — if it ever appeared in a chat,
  treat it as compromised.
- Change `ADMIN_PASSWORD` from any default value.
- Never commit or share the real `.env`.