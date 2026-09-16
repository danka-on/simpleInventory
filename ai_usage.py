"""Claude and OpenAI API usage ledger for the Tools page.

Neither provider offers an API that returns the remaining prepaid credit for a normal API key,
so every app call records its token usage here, priced from the table below. The owner types
the balance shown on the provider console; leftover is that balance minus what the app has
spent since it was entered. Spend from outside this app (the console, other tools) is not seen.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import threading

from flask import jsonify, request

DB_PATH = Path(__file__).resolve().parent / 'ai_usage.db'
PROVIDERS = ('anthropic', 'openai')
_lock = threading.Lock()

# USD per million tokens: input, output, cache write, cache read. Matched by model-name prefix.
CLAUDE_PRICES = (
    ('claude-haiku-4-5', 1.00, 5.00, 1.25, 0.10),
    ('claude-sonnet-4', 3.00, 15.00, 3.75, 0.30),
    ('claude-opus-4-5', 5.00, 25.00, 6.25, 0.50),
    ('claude-opus-4', 15.00, 75.00, 18.75, 1.50),
    ('claude-3-5-haiku', 0.80, 4.00, 1.00, 0.08),
)
# USD per million tokens: text input, audio input, output.
OPENAI_TOKEN_PRICES = (
    ('gpt-4o-mini-transcribe', 1.25, 3.00, 5.00),
    ('gpt-4o-transcribe', 2.50, 6.00, 10.00),
)
WHISPER_PER_MINUTE = 0.006


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute('''CREATE TABLE IF NOT EXISTS ai_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        feature TEXT NOT NULL,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        audio_seconds REAL DEFAULT 0,
        cost_usd REAL
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS ai_usage_time ON ai_usage (provider, created_at)')
    conn.execute('''CREATE TABLE IF NOT EXISTS ai_balance (
        provider TEXT PRIMARY KEY,
        amount_usd REAL NOT NULL,
        set_at TEXT NOT NULL,
        after_usage_id INTEGER NOT NULL DEFAULT 0
    )''')
    return conn


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _claude_cost(model, usage):
    for prefix, inp, out, write, read in CLAUDE_PRICES:
        if model.startswith(prefix):
            return (_int(usage.get('input_tokens')) * inp + _int(usage.get('output_tokens')) * out
                    + _int(usage.get('cache_creation_input_tokens')) * write
                    + _int(usage.get('cache_read_input_tokens')) * read) / 1e6
    return None


def _openai_cost(model, usage):
    if usage.get('type') == 'duration' or model.startswith('whisper'):
        return float(usage.get('seconds') or 0) / 60 * WHISPER_PER_MINUTE
    details = usage.get('input_token_details') or {}
    for prefix, text_in, audio_in, out in OPENAI_TOKEN_PRICES:
        if model.startswith(prefix):
            audio = _int(details.get('audio_tokens'))
            text = _int(details.get('text_tokens')) if details else _int(usage.get('input_tokens'))
            return (text * text_in + audio * audio_in + _int(usage.get('output_tokens')) * out) / 1e6
    return None


def record(provider, model, feature, result):
    """Log one successful API response body. Never raises: usage tracking must not break a feature."""
    try:
        usage = (result or {}).get('usage') if isinstance(result, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        model = str((result or {}).get('model') or model or 'unknown') if provider == 'anthropic' else str(model)
        if provider == 'anthropic':
            cost = _claude_cost(model, usage)
            inp = (_int(usage.get('input_tokens')) + _int(usage.get('cache_creation_input_tokens'))
                   + _int(usage.get('cache_read_input_tokens')))
            seconds = 0
        else:
            cost = _openai_cost(model, usage)
            inp = _int(usage.get('input_tokens'))
            seconds = float(usage.get('seconds') or 0)
        with _lock, closing(_connect()) as conn, conn:
            conn.execute('INSERT INTO ai_usage (created_at, provider, model, feature, input_tokens, output_tokens,'
                         ' audio_seconds, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                         (_now(), provider, model, str(feature), inp, _int(usage.get('output_tokens')),
                          seconds, cost))
    except Exception:
        pass


def summary():
    now = datetime.now(timezone.utc)
    windows = {
        'today': now.strftime('%Y-%m-%d 00:00:00'),
        'week': (now - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S'),
        'month': (now - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S'),
        'all': '',
    }
    out = {}
    with closing(_connect()) as conn:
        balances = {row[0]: row[1:] for row in conn.execute(
            'SELECT provider, amount_usd, set_at, after_usage_id FROM ai_balance')}
        for provider in PROVIDERS:
            info = {}
            for name, since in windows.items():
                calls, inp, outp, cost, unpriced = conn.execute(
                    'SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),'
                    ' COALESCE(SUM(cost_usd),0), SUM(cost_usd IS NULL) FROM ai_usage'
                    ' WHERE provider=? AND created_at>=?', (provider, since)).fetchone()
                info[name] = {'calls': calls, 'input_tokens': inp, 'output_tokens': outp,
                              'cost_usd': round(cost, 4), 'unpriced_calls': unpriced or 0}
            info['features'] = [
                {'feature': f, 'model': m, 'calls': c, 'cost_usd': round(s or 0, 4)}
                for f, m, c, s in conn.execute(
                    'SELECT feature, model, COUNT(*), SUM(cost_usd) FROM ai_usage WHERE provider=? AND created_at>=?'
                    ' GROUP BY feature, model ORDER BY SUM(cost_usd) DESC', (provider, windows['month']))]
            if provider in balances:
                amount, set_at, after_id = balances[provider]
                spent = conn.execute('SELECT COALESCE(SUM(cost_usd),0) FROM ai_usage WHERE provider=? AND id>?',
                                     (provider, after_id)).fetchone()[0]
                info['balance'] = {'entered_usd': amount, 'set_at': set_at, 'spent_since_usd': round(spent, 4),
                                   'leftover_usd': round(amount - spent, 2)}
            else:
                info['balance'] = None
            out[provider] = info
    return out


def register(app):
    @app.route('/api/ai-usage', methods=['GET'])
    def ai_usage_summary():
        try:
            response = jsonify(success=True, providers=summary())
            response.headers['Cache-Control'] = 'no-store'
            return response
        except Exception:
            app.logger.exception('AI usage summary failed')
            return jsonify(success=False, error='Unable to load API usage.'), 500

    @app.route('/api/ai-usage/balance', methods=['POST'])
    def ai_usage_balance():
        data = request.get_json(silent=True) or {}
        provider = data.get('provider')
        try:
            amount = float(data.get('amount_usd'))
        except (TypeError, ValueError):
            amount = -1
        if provider not in PROVIDERS or not 0 <= amount < 1e6:
            return jsonify(success=False, error='Choose Claude or ChatGPT and enter a balance in dollars.'), 400
        with _lock, closing(_connect()) as conn, conn:
            last_id = conn.execute('SELECT COALESCE(MAX(id), 0) FROM ai_usage').fetchone()[0]
            conn.execute('INSERT OR REPLACE INTO ai_balance (provider, amount_usd, set_at, after_usage_id) VALUES (?, ?, ?, ?)',
                         (provider, amount, _now(), last_id))
        return jsonify(success=True, providers=summary())
