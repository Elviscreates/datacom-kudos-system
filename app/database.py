from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator


SEED_USERS = (
    ("admin", "Morgan Admin", "People Operations", "admin"),
    ("alex", "Alex Chen", "Engineering", "employee"),
    ("sam", "Sam Rivera", "Design", "employee"),
    ("jordan", "Jordan Lee", "Customer Success", "employee"),
    ("taylor", "Taylor Kim", "Engineering", "employee"),
)

SEED_KUDOS = (
    ("alex", "sam", "Thanks for making the new onboarding flow so clear.", 3),
    ("jordan", "alex", "Your release notes made the handoff effortless.", 2),
    ("taylor", "jordan", "Thanks for bringing the customer feedback back to the team.", 1),
)


def utc_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    team TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'employee')),
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
                );

                CREATE TABLE IF NOT EXISTS kudos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    giver_id TEXT NOT NULL REFERENCES users(id),
                    recipient_id TEXT NOT NULL REFERENCES users(id),
                    message TEXT NOT NULL CHECK (length(message) <= 500 AND length(trim(message)) > 0),
                    created_at TEXT NOT NULL,
                    is_visible INTEGER NOT NULL DEFAULT 1 CHECK (is_visible IN (0, 1)),
                    moderated_by TEXT REFERENCES users(id),
                    moderated_at TEXT,
                    reason_for_moderation TEXT,
                    moderation_action TEXT CHECK (
                        moderation_action IS NULL OR moderation_action IN ('hidden', 'deleted')
                    ),
                    CHECK (giver_id <> recipient_id),
                    CHECK (
                        (
                            is_visible = 1
                            AND moderated_by IS NULL
                            AND moderated_at IS NULL
                            AND reason_for_moderation IS NULL
                            AND moderation_action IS NULL
                        )
                        OR
                        (
                            is_visible = 0
                            AND moderated_by IS NOT NULL
                            AND moderated_at IS NOT NULL
                            AND length(trim(reason_for_moderation)) > 0
                            AND moderation_action IS NOT NULL
                        )
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_kudos_feed
                    ON kudos (is_visible, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_kudos_sender_created
                    ON kudos (giver_id, created_at DESC);
                """
            )

            user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if user_count == 0:
                connection.executemany(
                    """
                    INSERT INTO users (id, display_name, team, role)
                    VALUES (?, ?, ?, ?)
                    """,
                    SEED_USERS,
                )

            kudos_count = connection.execute("SELECT COUNT(*) FROM kudos").fetchone()[0]
            if kudos_count == 0:
                now = datetime.now(timezone.utc)
                connection.executemany(
                    """
                    INSERT INTO kudos (giver_id, recipient_id, message, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (giver, recipient, message, utc_timestamp(now - timedelta(days=days)))
                        for giver, recipient, message, days in SEED_KUDOS
                    ],
                )
