import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "skywatch.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                reponse TEXT NOT NULL,
                outils TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
    conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_conn()
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    finally:
        conn.close()


def add_history(question: str, reponse: str, outils: list[str]) -> None:
    conn = get_conn()
    try:
        with conn:
            conn.execute(
                "INSERT INTO history (question, reponse, outils) VALUES (?, ?, ?)",
                (question, reponse, json.dumps(outils)),
            )
    finally:
        conn.close()


def last_history(limit: int = 10) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, question, reponse, outils, created_at FROM history "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{**dict(r), "outils": json.loads(r["outils"])} for r in rows]
    finally:
        conn.close()
