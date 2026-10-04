"""SQLite store. One connection per call (WAL), tiny helpers. Writes are small and infrequent."""
import json
import sqlite3
import time
from contextlib import contextmanager

from .config import DB_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
  id TEXT PRIMARY KEY, source TEXT, kind TEXT, title TEXT, url TEXT, published REAL,
  symbols TEXT, sentiment REAL, score INTEGER DEFAULT 0, fetched REAL
);
CREATE INDEX IF NOT EXISTS news_pub ON news(published);

CREATE TABLE IF NOT EXISTS sentiment_hist (
  ts REAL, symbol TEXT, mentions INTEGER, sentiment REAL, mindshare REAL, velocity REAL,
  PRIMARY KEY (ts, symbol)
);

CREATE TABLE IF NOT EXISTS snapshots (
  ts REAL PRIMARY KEY, data TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, issued REAL, symbol TEXT, horizon_h INTEGER,
  p_up REAL, p_base REAL, components TEXT, features TEXT, price REAL, resolve_at REAL,
  outcome INTEGER, price_end REAL, ret REAL, brier REAL, brier_base REAL, source TEXT DEFAULT 'ensemble',
  note TEXT
);
CREATE INDEX IF NOT EXISTS pred_open ON predictions(outcome, resolve_at);
CREATE INDEX IF NOT EXISTS pred_sym ON predictions(symbol, horizon_h, issued);

CREATE TABLE IF NOT EXISTS model_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, symbol TEXT, horizon_h INTEGER, metrics TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, spec TEXT, enabled INTEGER DEFAULT 1,
  cooldown_min INTEGER DEFAULT 60, last_fired REAL, created REAL
);
CREATE TABLE IF NOT EXISTS alert_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id INTEGER, ts REAL, message TEXT, data TEXT
);

CREATE TABLE IF NOT EXISTS holdings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, kind TEXT, qty REAL, avg_cost REAL,
  currency TEXT DEFAULT 'USD', note TEXT, created REAL
);

CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY, title TEXT, created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, content TEXT, meta TEXT, ts REAL
);
CREATE INDEX IF NOT EXISTS chat_msg ON chat_messages(chat_id, id);

CREATE TABLE IF NOT EXISTS briefs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, content TEXT, model TEXT
);

CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY, v TEXT, ts REAL
);

CREATE TABLE IF NOT EXISTS api_usage (
  day TEXT, api TEXT, units INTEGER, PRIMARY KEY (day, api)
);

CREATE TABLE IF NOT EXISTS elfa_trending (
  ts REAL, sym TEXT, rank INTEGER, cur INTEGER, prev INTEGER, chg REAL, PRIMARY KEY (ts, sym)
);
CREATE INDEX IF NOT EXISTS elfa_tr_sym ON elfa_trending(sym, ts);

-- wallet addresses are personal: local DB only (never settings.json, never git, never sent to the AI)
CREATE TABLE IF NOT EXISTS wallets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chain TEXT, address TEXT UNIQUE, label TEXT,
  include INTEGER DEFAULT 1, created REAL
);

CREATE TABLE IF NOT EXISTS signal_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, sym TEXT, kind TEXT, setup TEXT, direction TEXT, tf TEXT,
  price REAL, resolve_at REAL, price_end REAL, ret REAL, hit INTEGER
);
CREATE INDEX IF NOT EXISTS sig_ev ON signal_events(sym, setup, ts);
CREATE INDEX IF NOT EXISTS sig_open ON signal_events(hit, resolve_at);
"""

MIGRATIONS = [  # (table, column, type): additive only
    ("predictions", "in80", "INTEGER"),
]


@contextmanager
def conn():
    c = sqlite3.connect(DB_FILE, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def q(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def x(sql, args=()):
    with conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def xmany(sql, rows):
    with conn() as c:
        c.executemany(sql, rows)


def put_snapshot(data):
    x("INSERT OR REPLACE INTO snapshots(ts, data) VALUES (?, ?)", (time.time(), json.dumps(data)))
