"""Stockage SQLite : matières, documents, cartes de révision, réglages."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS subjects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    color       TEXT NOT NULL DEFAULT '#6366f1',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id   INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    parent_id    INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL DEFAULT 'note',
    title        TEXT NOT NULL,
    content      TEXT NOT NULL DEFAULT '',
    data         TEXT,
    source_file  TEXT,
    source_name  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_subject ON documents(subject_id);
CREATE INDEX IF NOT EXISTS idx_documents_parent ON documents(parent_id);

CREATE TABLE IF NOT EXISTS cards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    front        TEXT NOT NULL,
    back         TEXT NOT NULL,
    ease         REAL NOT NULL DEFAULT 2.5,
    interval     INTEGER NOT NULL DEFAULT 0,
    reps         INTEGER NOT NULL DEFAULT 0,
    lapses       INTEGER NOT NULL DEFAULT 0,
    due          TEXT NOT NULL,
    last_review  TEXT
);
CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(due);
CREATE INDEX IF NOT EXISTS idx_cards_document ON cards(document_id);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    score        INTEGER NOT NULL,
    total        INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_cache (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""

DOC_LIST_COLUMNS = (
    "id, subject_id, parent_id, kind, title, source_name, created_at, updated_at, "
    "length(content) AS size"
)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return date.today().isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    if "data" in d and d["data"]:
        try:
            d["data"] = json.loads(d["data"])
        except json.JSONDecodeError:
            d["data"] = None
    return d


# --------------------------------------------------------------------------- réglages

def get_settings() -> dict[str, Any]:
    settings = dict(config.DEFAULT_SETTINGS)
    with connect() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            try:
                settings[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                pass
    return settings


def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    with connect() as conn:
        for key, value in values.items():
            if key not in config.DEFAULT_SETTINGS:
                continue
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
    return get_settings()


# --------------------------------------------------------------------------- matières

def list_subjects() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM documents d WHERE d.subject_id = s.id) AS doc_count "
            "FROM subjects s ORDER BY s.name COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


def create_subject(name: str, color: str | None = None) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO subjects(name, color, created_at) VALUES(?, ?, ?)",
            (name, color or "#6366f1", now()),
        )
        row = conn.execute("SELECT * FROM subjects WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def update_subject(subject_id: int, name: str | None, color: str | None) -> dict[str, Any] | None:
    with connect() as conn:
        if name is not None:
            conn.execute("UPDATE subjects SET name = ? WHERE id = ?", (name, subject_id))
        if color is not None:
            conn.execute("UPDATE subjects SET color = ? WHERE id = ?", (color, subject_id))
        row = conn.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    return dict(row) if row else None


def delete_subject(subject_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))


# --------------------------------------------------------------------------- documents

def list_documents(subject_id: int | None = None, query: str | None = None) -> list[dict[str, Any]]:
    sql = f"SELECT {DOC_LIST_COLUMNS} FROM documents"
    where, params = [], []
    if subject_id is not None:
        where.append("subject_id = ?")
        params.append(subject_id)
    if query:
        where.append("(title LIKE ? OR content LIKE ?)")
        like = f"%{query}%"
        params += [like, like]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at ASC, id ASC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def search_documents(query: str, limit: int = 30) -> list[dict[str, Any]]:
    """Recherche plein texte simple avec extrait autour de la première occurrence."""
    results = []
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, subject_id, kind, title, content FROM documents "
            "WHERE title LIKE ? OR content LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
    q = query.lower()
    for r in rows:
        content = r["content"] or ""
        idx = content.lower().find(q)
        snippet = ""
        if idx >= 0:
            start = max(0, idx - 60)
            snippet = ("…" if start else "") + content[start : idx + len(query) + 80].replace("\n", " ") + "…"
        results.append(
            {"id": r["id"], "subject_id": r["subject_id"], "kind": r["kind"], "title": r["title"], "snippet": snippet}
        )
    return results


def get_document(doc_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return row_to_dict(row)


def create_document(
    title: str,
    content: str = "",
    kind: str = "note",
    subject_id: int | None = None,
    parent_id: int | None = None,
    data: Any = None,
    source_file: str | None = None,
    source_name: str | None = None,
) -> dict[str, Any]:
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO documents(subject_id, parent_id, kind, title, content, data, source_file, "
            "source_name, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                subject_id,
                parent_id,
                kind,
                title,
                content,
                json.dumps(data, ensure_ascii=False) if data is not None else None,
                source_file,
                source_name,
                ts,
                ts,
            ),
        )
        doc_id = cur.lastrowid
    return get_document(doc_id)  # type: ignore[return-value]


def update_document(doc_id: int, **fields: Any) -> dict[str, Any] | None:
    allowed = {"title", "content", "kind", "subject_id", "parent_id", "data"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "data" and value is not None:
            value = json.dumps(value, ensure_ascii=False)
        sets.append(f"{key} = ?")
        params.append(value)
    if sets:
        sets.append("updated_at = ?")
        params.append(now())
        params.append(doc_id)
        with connect() as conn:
            conn.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id = ?", params)
    return get_document(doc_id)


def delete_document(doc_id: int) -> str | None:
    """Supprime un document et renvoie le nom du fichier source éventuel."""
    with connect() as conn:
        row = conn.execute("SELECT source_file FROM documents WHERE id = ?", (doc_id,)).fetchone()
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    return row["source_file"] if row else None


# --------------------------------------------------------------------------- cartes

def add_cards(document_id: int, cards: list[dict[str, str]]) -> None:
    due = today()
    with connect() as conn:
        conn.executemany(
            "INSERT INTO cards(document_id, front, back, due) VALUES(?, ?, ?, ?)",
            [(document_id, c["front"], c["back"], due) for c in cards],
        )


def list_cards(document_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM cards WHERE document_id = ? ORDER BY id", (document_id,)).fetchall()
    return [dict(r) for r in rows]


def get_card(card_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return dict(row) if row else None


def update_card(card_id: int, **fields: Any) -> dict[str, Any] | None:
    allowed = {"front", "back", "ease", "interval", "reps", "lapses", "due", "last_review"}
    sets = [(k, v) for k, v in fields.items() if k in allowed]
    if sets:
        with connect() as conn:
            conn.execute(
                f"UPDATE cards SET {', '.join(f'{k} = ?' for k, _ in sets)} WHERE id = ?",
                [v for _, v in sets] + [card_id],
            )
    return get_card(card_id)


def delete_card(card_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))


def due_cards(subject_id: int | None = None, document_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    sql = (
        "SELECT c.*, d.title AS deck_title, d.subject_id FROM cards c "
        "JOIN documents d ON d.id = c.document_id WHERE c.due <= ?"
    )
    params: list[Any] = [today()]
    if subject_id is not None:
        sql += " AND d.subject_id = ?"
        params.append(subject_id)
    if document_id is not None:
        sql += " AND c.document_id = ?"
        params.append(document_id)
    sql += " ORDER BY c.due, c.reps, c.id LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- quiz & stats

def add_quiz_attempt(document_id: int, score: int, total: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO quiz_attempts(document_id, score, total, created_at) VALUES(?,?,?,?)",
            (document_id, score, total, now()),
        )


def quiz_attempts(document_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM quiz_attempts WHERE document_id = ? ORDER BY created_at DESC LIMIT 20", (document_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict[str, Any]:
    with connect() as conn:
        kinds = {r["kind"]: r["n"] for r in conn.execute("SELECT kind, COUNT(*) AS n FROM documents GROUP BY kind")}
        due = conn.execute("SELECT COUNT(*) FROM cards WHERE due <= ?", (today(),)).fetchone()[0]
        total_cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        attempts = conn.execute(
            "SELECT q.score, q.total, q.created_at, d.title, d.id AS document_id FROM quiz_attempts q "
            "JOIN documents d ON d.id = q.document_id ORDER BY q.created_at DESC LIMIT 8"
        ).fetchall()
        recent = conn.execute(
            f"SELECT {DOC_LIST_COLUMNS} FROM documents ORDER BY updated_at DESC LIMIT 6"
        ).fetchall()
    return {
        "kinds": kinds,
        "due_cards": due,
        "total_cards": total_cards,
        "attempts": [dict(a) for a in attempts],
        "recent": [dict(r) for r in recent],
    }


# --------------------------------------------------------------------------- cache LLM

def cache_get(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM llm_cache WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def cache_set(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO llm_cache(key, value, created_at) VALUES(?, ?, ?)", (key, value, now())
        )
