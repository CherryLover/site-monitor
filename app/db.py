# ============================================================================
#  db —— SQLite 单文件存储
#
#  每次操作开一个短连接（WAL 模式），没有连接池、没有 ORM。
#  schema 用 CREATE IF NOT EXISTS 幂等创建；结构变化时直接改这里。
# ============================================================================
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  sub TEXT PRIMARY KEY, email TEXT, name TEXT,
  created_at TEXT NOT NULL, last_login_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_tokens (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, prefix TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, created_by TEXT NOT NULL,
  created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS secrets (
  key TEXT PRIMARY KEY, value BLOB NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
  key TEXT PRIMARY KEY, ok INTEGER NOT NULL, detail TEXT NOT NULL, checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY, domain TEXT NOT NULL UNIQUE,
  umami_website_id TEXT, gsc_property TEXT, hostname TEXT NOT NULL,
  brand_terms TEXT NOT NULL DEFAULT '[]', funnel_events TEXT NOT NULL DEFAULT '[]',
  position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gsc_daily (
  site_id INTEGER NOT NULL, date TEXT NOT NULL,
  clicks INTEGER NOT NULL, impressions INTEGER NOT NULL, position REAL NOT NULL,
  PRIMARY KEY (site_id, date)
);
CREATE TABLE IF NOT EXISTS gsc_rows (
  site_id INTEGER NOT NULL, date TEXT NOT NULL, query TEXT NOT NULL, page TEXT NOT NULL,
  clicks INTEGER NOT NULL, impressions INTEGER NOT NULL, position REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS gsc_rows_site_date ON gsc_rows (site_id, date);
CREATE TABLE IF NOT EXISTS gsc_country (
  site_id INTEGER NOT NULL, date TEXT NOT NULL, query TEXT NOT NULL, country TEXT NOT NULL,
  clicks INTEGER NOT NULL, impressions INTEGER NOT NULL, position REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS gsc_country_site_date ON gsc_country (site_id, date);
CREATE TABLE IF NOT EXISTS inspections (
  site_id INTEGER NOT NULL, url TEXT NOT NULL, coverage TEXT NOT NULL,
  last_crawl TEXT, google_canonical TEXT, checked_at TEXT NOT NULL,
  PRIMARY KEY (site_id, url)
);
CREATE TABLE IF NOT EXISTS psi_runs (
  site_id INTEGER NOT NULL, url TEXT NOT NULL, strategy TEXT NOT NULL, run_at TEXT NOT NULL,
  performance INTEGER, seo INTEGER, accessibility INTEGER, best_practices INTEGER,
  lcp_ms REAL, cls REAL, tbt_ms REAL,
  PRIMARY KEY (site_id, url, strategy)
);
CREATE TABLE IF NOT EXISTS changelog (
  id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL, date TEXT NOT NULL,
  page TEXT, summary TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opp_state (
  site_id INTEGER NOT NULL, key TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (site_id, key)
);
CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL, key TEXT NOT NULL, type TEXT NOT NULL,
  target TEXT NOT NULL, evidence TEXT NOT NULL, scope TEXT, history TEXT NOT NULL,
  fixed_at TEXT NOT NULL, verdict TEXT NOT NULL, summary TEXT, verified_at TEXT, checked_at TEXT,
  created_at TEXT NOT NULL, UNIQUE (site_id, key)
);
CREATE TABLE IF NOT EXISTS watch_points (
  watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE, date TEXT NOT NULL,
  verdict TEXT NOT NULL, summary TEXT, before TEXT, after TEXT,
  PRIMARY KEY (watch_id, date)
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, site_id INTEGER, kind TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, ok INTEGER, detail TEXT
);
"""

# 已上线的库不会因为 CREATE IF NOT EXISTS 获得新列：在这里补，(表, 列, 定义, 回填 SQL)
MIGRATIONS = [
    ("sites", "position", "INTEGER NOT NULL DEFAULT 0", "UPDATE sites SET position = id"),
]


class DB:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)
            for table, col, ddl, backfill in MIGRATIONS:
                cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                    c.execute(backfill)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def all(self, sql: str, *args) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def one(self, sql: str, *args) -> dict | None:
        rows = self.all(sql, *args)
        return rows[0] if rows else None

    def run(self, sql: str, *args) -> int:
        with self.conn() as c:
            return c.execute(sql, args).lastrowid or 0

    def many(self, sql: str, rows: list[tuple]) -> None:
        with self.conn() as c:
            c.executemany(sql, rows)
