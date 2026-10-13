from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import create_engine, event, select, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from .models import (
    Base,
    Court,
    SchemaMigration,
    QueueEntry,
    QueueGroup,
    SchedulerLock,
    utcnow,
)


class Database:
    def __init__(self, url: str):
        sqlite = url.startswith("sqlite:")
        if sqlite and "///" in url and not url.endswith(":memory:"):
            Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        args = (
            {"check_same_thread": False, "timeout": 15}
            if sqlite
            else {"connect_timeout": 2, "read_timeout": 30, "write_timeout": 2}
        )
        self.engine = create_engine(url, pool_pre_ping=True, connect_args=args)
        if sqlite:

            @event.listens_for(self.engine, "connect")
            def pragmas(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self):
        with self.sessions.begin() as session:
            yield session

    @contextmanager
    def assignment_transaction(self):
        """Serialize assignment writers across processes, including SQLite tests."""
        if self.engine.dialect.name == "sqlite":
            with self.engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                with Session(bind=connection, expire_on_commit=False) as session:
                    try:
                        yield session
                        session.flush()
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
        else:
            with self.transaction() as session:
                session.get(SchedulerLock, 1, with_for_update=True)
                yield session

    def migrate(self):
        Base.metadata.create_all(self.engine)
        additions = {
            "play_sessions": {"event_type": "VARCHAR(20) NOT NULL DEFAULT 'OPEN_PLAY'", "event_date": "VARCHAR(10) NULL", "ended_at": "DATETIME NULL"},
            "player_checkins": {"session_id": "INTEGER NULL"},
            "tournaments": {"session_id": "INTEGER NULL"},
            "courts": {
                "enabled": "INTEGER NOT NULL DEFAULT 1",
                "available_since": "DATETIME NULL",
                "level": "INTEGER NOT NULL DEFAULT 0",
            },
            "player_queue": {"group_id": "INTEGER NULL"},
            "matches": {
                "matchmaking_mode": "VARCHAR(30) NOT NULL DEFAULT 'LEGACY_WEIGHTED'",
                "session_id": "INTEGER NULL",
                "matchmaking_json": "JSON NULL",
                "queue_groups_json": "JSON NULL",
            },
        }
        # Resumable additive DDL: MariaDB implicitly commits each ALTER.
        for table, columns in additions.items():
            existing = {c["name"] for c in inspect(self.engine).get_columns(table)}
            for column, definition in columns.items():
                if column not in existing:
                    with self.engine.begin() as connection:
                        connection.execute(
                            text(
                                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                            )
                        )
        with self.transaction() as s:
            if not s.get(SchemaMigration, 1):
                s.add(SchemaMigration(version=1))
            if not s.get(SchemaMigration, 2):
                s.add(SchemaMigration(version=2))
            if not s.get(SchedulerLock, 1):
                s.add(SchedulerLock(id=1))
            if not s.get(SchemaMigration, 3):
                for court in s.scalars(select(Court)):
                    court.enabled = 0 if court.status == "DISABLED" else 1
                    if court.status == "AVAILABLE":
                        court.available_since = court.updated_at or utcnow()
                for entry in s.scalars(
                    select(QueueEntry).where(QueueEntry.group_id.is_(None))
                ):
                    group = QueueGroup(
                        entry_type="SOLO",
                        status=entry.status,
                        created_at=entry.joined_at,
                    )
                    s.add(group)
                    s.flush()
                    entry.group_id = group.id
                s.add(SchemaMigration(version=3))
            if not s.scalar(select(Court.id).limit(1)):
                s.add(Court(name="Court 1", number=1))
