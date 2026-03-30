"""SQLite persistence for handoff sessions, lanes, and checkpoints.

Uses stdlib sqlite3 only — no external dependencies.
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


class HandoffStore:
    """SQLite-backed durable storage for agent handoff coordination.

    Three tables: handoff_sessions, handoff_lanes, handoff_checkpoints.
    All complex fields (decisions, files_touched, todos, metadata, context, snapshot)
    stored as JSON text — no junction tables needed.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS handoff_sessions (
                id          TEXT PRIMARY KEY,
                agent_id    TEXT NOT NULL,
                repo        TEXT,
                status      TEXT NOT NULL DEFAULT 'active',
                task_summary TEXT,
                decisions   TEXT DEFAULT '[]',
                files_touched TEXT DEFAULT '[]',
                todos       TEXT DEFAULT '[]',
                metadata    TEXT DEFAULT '{}',
                created     TEXT NOT NULL,
                updated     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS handoff_lanes (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES handoff_sessions(id),
                from_agent  TEXT NOT NULL,
                to_agent    TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'open',
                context     TEXT DEFAULT '{}',
                created     TEXT NOT NULL,
                updated     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS handoff_checkpoints (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES handoff_sessions(id),
                lane_id     TEXT REFERENCES handoff_lanes(id),
                agent_id    TEXT NOT NULL,
                snapshot    TEXT NOT NULL,
                created     TEXT NOT NULL
            );
        """)

    # ── Sessions ──

    def save_session(
        self,
        agent_id: str,
        repo: Optional[str] = None,
        status: str = "active",
        task_summary: Optional[str] = None,
        decisions: Optional[List] = None,
        files_touched: Optional[List] = None,
        todos: Optional[List] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        sid = _uid()
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO handoff_sessions
                   (id, agent_id, repo, status, task_summary,
                    decisions, files_touched, todos, metadata, created, updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid, agent_id, repo, status, task_summary,
                    json.dumps(decisions or []),
                    json.dumps(files_touched or []),
                    json.dumps(todos or []),
                    json.dumps(metadata or {}),
                    now, now,
                ),
            )
            self._conn.commit()
        return sid

    def get_session(
        self,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict]:
        with self._lock:
            if session_id:
                row = self._conn.execute(
                    "SELECT * FROM handoff_sessions WHERE id = ?", (session_id,)
                ).fetchone()
            elif agent_id:
                row = self._conn.execute(
                    "SELECT * FROM handoff_sessions WHERE agent_id = ? ORDER BY updated DESC LIMIT 1",
                    (agent_id,),
                ).fetchone()
            else:
                return None
        if row is None:
            return None
        return self._row_to_session(row)

    def list_sessions(
        self,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict]:
        clauses: List[str] = []
        params: List[Any] = []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM handoff_sessions{where} ORDER BY updated DESC",
                params,
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def update_session(self, session_id: str, **kwargs: Any) -> None:
        allowed = {
            "status", "task_summary", "repo",
            "decisions", "files_touched", "todos", "metadata",
        }
        json_fields = {"decisions", "files_touched", "todos", "metadata"}
        sets: List[str] = []
        params: List[Any] = []
        for k, v in kwargs.items():
            if k not in allowed:
                continue
            if k in json_fields:
                v = json.dumps(v)
            sets.append(f"{k} = ?")
            params.append(v)
        if not sets:
            return
        sets.append("updated = ?")
        params.append(_now())
        params.append(session_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE handoff_sessions SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            self._conn.commit()

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Dict:
        d = dict(row)
        for field in ("decisions", "files_touched", "todos", "metadata"):
            if d.get(field):
                d[field] = json.loads(d[field])
        return d

    # ── Lanes ──

    def open_lane(
        self,
        session_id: str,
        from_agent: str,
        to_agent: str,
        context: Optional[Dict] = None,
    ) -> str:
        lid = _uid()
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO handoff_lanes
                   (id, session_id, from_agent, to_agent, status, context, created, updated)
                   VALUES (?, ?, ?, ?, 'open', ?, ?, ?)""",
                (lid, session_id, from_agent, to_agent, json.dumps(context or {}), now, now),
            )
            self._conn.commit()
        return lid

    def get_lane(self, lane_id: str) -> Optional[Dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM handoff_lanes WHERE id = ?", (lane_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_lane(row)

    def list_lanes(self, session_id: Optional[str] = None) -> List[Dict]:
        with self._lock:
            if session_id:
                rows = self._conn.execute(
                    "SELECT * FROM handoff_lanes WHERE session_id = ? ORDER BY created DESC",
                    (session_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM handoff_lanes ORDER BY created DESC"
                ).fetchall()
        return [self._row_to_lane(r) for r in rows]

    def close_lane(self, lane_id: str) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE handoff_lanes SET status = 'closed', updated = ? WHERE id = ?",
                (now, lane_id),
            )
            self._conn.commit()

    @staticmethod
    def _row_to_lane(row: sqlite3.Row) -> Dict:
        d = dict(row)
        if d.get("context"):
            d["context"] = json.loads(d["context"])
        return d

    # ── Checkpoints ──

    def checkpoint(
        self,
        session_id: str,
        agent_id: str,
        snapshot: Dict,
        lane_id: Optional[str] = None,
    ) -> str:
        cid = _uid()
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO handoff_checkpoints
                   (id, session_id, lane_id, agent_id, snapshot, created)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (cid, session_id, lane_id, agent_id, json.dumps(snapshot), now),
            )
            self._conn.commit()
        return cid

    def list_checkpoints(
        self,
        session_id: Optional[str] = None,
        lane_id: Optional[str] = None,
    ) -> List[Dict]:
        clauses: List[str] = []
        params: List[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if lane_id:
            clauses.append("lane_id = ?")
            params.append(lane_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM handoff_checkpoints{where} ORDER BY created DESC",
                params,
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("snapshot"):
                d["snapshot"] = json.loads(d["snapshot"])
            result.append(d)
        return result

    # ── Lifecycle ──

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
