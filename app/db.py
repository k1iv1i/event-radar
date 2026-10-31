"""SQLite 存储层：文档、主张、事件、版本、通知。"""
import json
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  source TEXT,
  publisher TEXT,
  url TEXT,
  event_time TEXT,
  disclose_time TEXT,
  crawl_time TEXT,
  body TEXT,
  body_complete INTEGER DEFAULT 1,
  fetch_status TEXT DEFAULT 'ok'
);
CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  event_id TEXT,
  claim_type TEXT NOT NULL,
  summary TEXT,
  quote TEXT,
  subjects TEXT,
  core_proposition TEXT,
  event_time TEXT,
  evidence_weight INTEGER,
  extraction_method TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  entity_tags TEXT,
  status TEXT DEFAULT 'emerging',
  conclusion TEXT,
  event_time TEXT,
  latest_disclose TEXT,
  created_at TEXT,
  updated_at TEXT,
  primary_doc_id TEXT
);
CREATE TABLE IF NOT EXISTS versions (
  version_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL,
  version_no INTEGER NOT NULL,
  change_type TEXT NOT NULL,
  trigger_doc_id TEXT,
  summary TEXT,
  conclusion_before TEXT,
  conclusion_after TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
  notif_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT,
  notif_type TEXT NOT NULL,
  title TEXT,
  message TEXT,
  created_at TEXT,
  read INTEGER DEFAULT 0
);
"""


@contextmanager
def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def reset_db():
    with connect() as conn:
        conn.executescript(
            "DROP TABLE IF EXISTS documents; DROP TABLE IF EXISTS claims;"
            " DROP TABLE IF EXISTS events; DROP TABLE IF EXISTS versions;"
            " DROP TABLE IF EXISTS notifications;"
        )
        conn.executescript(SCHEMA)


# ---------- documents ----------

def insert_document(doc: dict):
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO documents
               (doc_id, title, source, publisher, url, event_time, disclose_time,
                crawl_time, body, body_complete, fetch_status)
               VALUES (:doc_id, :title, :source, :publisher, :url, :event_time,
                       :disclose_time, :crawl_time, :body, :body_complete, :fetch_status)""",
            doc,
        )


def get_document(doc_id: str):
    with connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        return dict(row) if row else None


def list_documents():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM documents ORDER BY disclose_time").fetchall()
        return [dict(r) for r in rows]


# ---------- claims ----------

def insert_claim(claim: dict):
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO claims
               (claim_id, doc_id, event_id, claim_type, summary, quote, subjects,
                core_proposition, event_time, evidence_weight, extraction_method)
               VALUES (:claim_id, :doc_id, :event_id, :claim_type, :summary, :quote,
                       :subjects, :core_proposition, :event_time, :evidence_weight,
                       :extraction_method)""",
            claim,
        )


def list_claims_for_event(event_id: str):
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM claims WHERE event_id=? ORDER BY event_time", (event_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- events ----------

def insert_event(event: dict):
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO events
               (event_id, title, entity_tags, status, conclusion, event_time,
                latest_disclose, created_at, updated_at, primary_doc_id)
               VALUES (:event_id, :title, :entity_tags, :status, :conclusion,
                       :event_time, :latest_disclose, :created_at, :updated_at,
                       :primary_doc_id)""",
            event,
        )


def get_event(event_id: str):
    with connect() as conn:
        row = conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        return dict(row) if row else None


def list_events():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def count_event_claims(event_id: str) -> dict:
    with connect() as conn:
        rows = conn.execute(
            "SELECT claim_type, COUNT(*) n FROM claims WHERE event_id=? GROUP BY claim_type",
            (event_id,),
        ).fetchall()
        counts = {"fact": 0, "opinion": 0, "speculation": 0, "rumor": 0}
        for r in rows:
            counts[r["claim_type"]] = r["n"]
        return counts


# ---------- versions ----------

def insert_version(v: dict):
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version_no),0)+1 n FROM versions WHERE event_id=?",
            (v["event_id"],),
        ).fetchone()
        v = dict(v, version_no=row["n"])
        conn.execute(
            """INSERT INTO versions (event_id, version_no, change_type, trigger_doc_id,
               summary, conclusion_before, conclusion_after, created_at)
               VALUES (:event_id, :version_no, :change_type, :trigger_doc_id, :summary,
                       :conclusion_before, :conclusion_after, :created_at)""",
            v,
        )
        return v


def list_versions(event_id: str):
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM versions WHERE event_id=? ORDER BY version_no", (event_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- notifications ----------

def insert_notification(n: dict):
    with connect() as conn:
        conn.execute(
            """INSERT INTO notifications (event_id, notif_type, title, message, created_at, read)
               VALUES (:event_id, :notif_type, :title, :message, :created_at, 0)""",
            n,
        )


def list_notifications(limit: int = 50):
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications ORDER BY notif_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def mark_notification_read(notif_id: int):
    with connect() as conn:
        conn.execute("UPDATE notifications SET read=1 WHERE notif_id=?", (notif_id,))


def has_notification_of_type(event_id: str, notif_type: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM notifications WHERE event_id=? AND notif_type=? LIMIT 1",
            (event_id, notif_type),
        ).fetchone()
        return row is not None
