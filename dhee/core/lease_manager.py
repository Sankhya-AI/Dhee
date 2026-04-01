"""Dhee v3 — SQLite Lease Manager for job concurrency control.

Ensures that only one runner can execute a given maintenance job at a time.
Uses SQLite's BEGIN IMMEDIATE for atomic lease acquisition.

Design contract:
    - Leases are time-bounded (default 300s)
    - Expired leases are automatically stolen
    - Renew extends lease while holding it
    - Release is explicit; stale leases cleaned on next acquire
    - Zero external dependencies — pure SQLite
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_LEASE_DURATION_SECONDS = 300


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


class LeaseManager:
    """SQLite-based distributed lease manager.

    Each lock_id represents a named resource (e.g., a job name).
    Only one owner can hold a lease at a time. Expired leases are
    automatically reclaimed.

    Usage:
        lm = LeaseManager(conn, lock)
        acquired = lm.acquire("distill_batch", owner_id="worker-1")
        if acquired:
            try:
                # do work
                lm.renew("distill_batch", "worker-1")  # extend if long
            finally:
                lm.release("distill_batch", "worker-1")
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: threading.RLock,
        *,
        default_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
    ):
        self._conn = conn
        self._lock = lock
        self.default_duration = default_duration_seconds

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def acquire(
        self,
        lock_id: str,
        owner_id: str,
        *,
        duration_seconds: Optional[int] = None,
    ) -> bool:
        """Try to acquire a lease. Returns True if acquired.

        If the lock is held by another owner and not expired, returns False.
        If the lock is expired, steals it (atomic via BEGIN IMMEDIATE).
        If the lock is held by the same owner, renews it.
        """
        duration = duration_seconds or self.default_duration
        now = _utcnow()
        expires = (now + timedelta(seconds=duration)).isoformat()
        now_iso = now.isoformat()

        with self._tx() as conn:
            row = conn.execute(
                "SELECT owner_id, lease_expires_at FROM locks WHERE lock_id = ?",
                (lock_id,),
            ).fetchone()

            if row is None:
                # No lock exists — create it
                conn.execute(
                    """INSERT INTO locks (lock_id, owner_id, lease_expires_at, updated_at)
                       VALUES (?, ?, ?, ?)""",
                    (lock_id, owner_id, expires, now_iso),
                )
                return True

            existing_owner = row["owner_id"]
            existing_expires = row["lease_expires_at"]

            # Same owner — renew
            if existing_owner == owner_id:
                conn.execute(
                    "UPDATE locks SET lease_expires_at = ?, updated_at = ? WHERE lock_id = ?",
                    (expires, now_iso, lock_id),
                )
                return True

            # Different owner — check if expired
            try:
                exp_dt = datetime.fromisoformat(existing_expires.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                exp_dt = _utcnow()  # treat unparseable as expired

            if now >= exp_dt:
                # Expired — steal the lease
                conn.execute(
                    "UPDATE locks SET owner_id = ?, lease_expires_at = ?, updated_at = ? WHERE lock_id = ?",
                    (owner_id, expires, now_iso, lock_id),
                )
                logger.info(
                    "Lease %s stolen from %s (expired %s) by %s",
                    lock_id, existing_owner, existing_expires, owner_id,
                )
                return True

            # Not expired — someone else holds it
            return False

    def release(self, lock_id: str, owner_id: str) -> bool:
        """Release a lease. Returns True if successfully released.

        Only the current owner can release. Returns False if:
        - Lock doesn't exist
        - Lock is held by a different owner
        """
        with self._tx() as conn:
            row = conn.execute(
                "SELECT owner_id FROM locks WHERE lock_id = ?",
                (lock_id,),
            ).fetchone()

            if not row or row["owner_id"] != owner_id:
                return False

            conn.execute("DELETE FROM locks WHERE lock_id = ?", (lock_id,))
            return True

    def renew(
        self,
        lock_id: str,
        owner_id: str,
        *,
        duration_seconds: Optional[int] = None,
    ) -> bool:
        """Extend a lease. Returns True if renewed.

        Only the current owner can renew. Returns False if:
        - Lock doesn't exist
        - Lock is held by a different owner
        - Lock has already expired (use acquire to re-take)
        """
        duration = duration_seconds or self.default_duration
        now = _utcnow()
        expires = (now + timedelta(seconds=duration)).isoformat()
        now_iso = now.isoformat()

        with self._tx() as conn:
            row = conn.execute(
                "SELECT owner_id, lease_expires_at FROM locks WHERE lock_id = ?",
                (lock_id,),
            ).fetchone()

            if not row or row["owner_id"] != owner_id:
                return False

            # Check not expired
            try:
                exp_dt = datetime.fromisoformat(
                    row["lease_expires_at"].replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                return False

            if now >= exp_dt:
                return False  # expired — must re-acquire

            conn.execute(
                "UPDATE locks SET lease_expires_at = ?, updated_at = ? WHERE lock_id = ?",
                (expires, now_iso, lock_id),
            )
            return True

    def is_held(self, lock_id: str) -> bool:
        """Check if a lock is currently held (not expired)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT lease_expires_at FROM locks WHERE lock_id = ?",
                (lock_id,),
            ).fetchone()

        if not row:
            return False

        try:
            exp_dt = datetime.fromisoformat(
                row["lease_expires_at"].replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            return False

        return _utcnow() < exp_dt

    def get_holder(self, lock_id: str) -> Optional[str]:
        """Get the current holder of a lock, or None if unheld/expired."""
        with self._lock:
            row = self._conn.execute(
                "SELECT owner_id, lease_expires_at FROM locks WHERE lock_id = ?",
                (lock_id,),
            ).fetchone()

        if not row:
            return None

        try:
            exp_dt = datetime.fromisoformat(
                row["lease_expires_at"].replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            return None

        if _utcnow() >= exp_dt:
            return None

        return row["owner_id"]

    def cleanup_expired(self) -> int:
        """Remove all expired lease rows. Returns count removed."""
        now_iso = _utcnow_iso()
        with self._tx() as conn:
            result = conn.execute(
                "DELETE FROM locks WHERE lease_expires_at < ?",
                (now_iso,),
            )
        return result.rowcount
