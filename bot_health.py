"""Local database heartbeat; never store tokens or raw exception messages."""
from contextlib import closing
import asyncio
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone


def write_status(database, state):
    try:
        with closing(sqlite3.connect(str(database), timeout=2)) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS bot_health (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, checked REAL NOT NULL)')
            conn.execute('INSERT OR REPLACE INTO bot_health VALUES (1, ?, ?)', (state, time.time()))
    except sqlite3.Error:
        pass  # Monitoring must not interrupt worker requests.


def read_status(folder, config, now=None):
    def result(state, label, detail, checked=''):
        return dict(state=state, label=label, detail=detail, checked=checked)
    if not config.get('TELEGRAM_BOT_TOKEN'):
        return result('unknown', 'Not configured', 'Add a bot token first.')
    database = Path(config.get('DB_PATH') or 'worker_bot.db')
    if not database.is_absolute():
        database = Path(folder) / database
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as conn:
            row = conn.execute('SELECT state, checked FROM bot_health WHERE id=1').fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return result('unknown', 'Not monitored yet', 'Restart the updated bot to begin monitoring. Remote bots need their own status connection.')
    state, checked = row
    stamp = datetime.fromtimestamp(checked, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    if (time.time() if now is None else now) - checked > 100:
        return result('stale', 'No recent update', 'Bot may be stopped or unreachable. Check its terminal.', stamp)
    labels = {'running': ('Running', 'Bot polling is active and Telegram connection checked.'),
              'starting': ('Starting', 'Waiting for Telegram polling to start.'),
              'stopped': ('Stopped', 'Bot has shut down.'),
              'connection_error': ('Connection error', 'Cannot reach Telegram. Check internet and bot token.'),
              'conflict': ('Polling conflict', 'Another process or webhook may be using this token.')}
    label, detail = labels.get(state, ('Unknown', 'Restart the bot and check its terminal.'))
    return result(state, label, detail, stamp)


def install(application, database):
    from telegram.error import Conflict, NetworkError, InvalidToken
    recent_error = {'until': 0, 'state': ''}

    async def monitor(app):
        while True:
            state = 'starting'
            try:
                await asyncio.wait_for(app.bot.get_me(), timeout=12)
                if app.updater and app.updater.running:
                    state = 'running'
            except asyncio.CancelledError:
                raise
            except Exception:
                state = 'connection_error'
            if recent_error['until'] > time.time():
                state = recent_error['state']
            write_status(database, state)
            await asyncio.sleep(30)

    async def startup(app):
        write_status(database, 'starting')
        app.bot_data['_health_task'] = asyncio.create_task(monitor(app))

    async def shutdown(app):
        task = app.bot_data.pop('_health_task', None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        write_status(database, 'stopped')

    async def error(update, context):
        if isinstance(context.error, (Conflict, NetworkError, InvalidToken)):
            state = 'conflict' if isinstance(context.error, Conflict) else 'connection_error'
            recent_error.update(until=time.time() + 90, state=state)
            write_status(database, state)

    application.post_init = startup
    application.post_shutdown = shutdown
    application.add_error_handler(error)

