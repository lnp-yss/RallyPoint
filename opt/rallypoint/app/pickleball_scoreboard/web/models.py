"""Portable relational models: MariaDB production, SQLite development/tests."""

from datetime import datetime, timezone
from sqlalchemy import (
    ForeignKey,
    String,
    Integer,
    Float,
    Text,
    DateTime,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class AuditMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class User(Base, AuditMixin):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="VIEWER")
    active: Mapped[int] = mapped_column(Integer, default=1)


class Player(Base, AuditMixin):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(80), default="")
    last_name: Mapped[str] = mapped_column(String(80), default="")
    display_name: Mapped[str] = mapped_column(String(100))
    nickname: Mapped[str] = mapped_column(String(80), default="")
    gender: Mapped[str] = mapped_column(String(30), default="")
    skill_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating_type: Mapped[str] = mapped_column(String(30), default="Manual")
    games_played: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class Court(Base, AuditMixin):
    __tablename__ = "courts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    number: Mapped[int] = mapped_column(Integer, unique=True)
    status: Mapped[str] = mapped_column(String(20), default="AVAILABLE")
    active_match_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    available_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    level: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ScoreboardDevice(Base):
    __tablename__ = "scoreboard_devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_name: Mapped[str] = mapped_column(String(100), unique=True)
    court_id: Mapped[int] = mapped_column(ForeignKey("courts.id"), unique=True)
    hostname: Mapped[str] = mapped_column(String(150), default="")
    ip_address: Mapped[str] = mapped_column(String(50), default="")
    bridge_url: Mapped[str] = mapped_column(
        String(200), default="http://127.0.0.1:8765"
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="OFFLINE")


class Match(Base, AuditMixin):
    __tablename__ = "matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    court_id: Mapped[int] = mapped_column(ForeignKey("courts.id"))
    match_type: Mapped[str] = mapped_column(String(20))
    scoring_type: Mapped[str] = mapped_column(String(20))
    points_to_win: Mapped[int] = mapped_column(Integer)
    win_by: Mapped[int] = mapped_column(Integer)
    best_of: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="PREPARED", index=True)
    settings_json: Mapped[dict] = mapped_column(JSON)
    state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    starting_side: Mapped[str] = mapped_column(String(10), default="Right")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    winner_team: Mapped[str | None] = mapped_column(String(1), nullable=True)
    matchmaking_mode: Mapped[str] = mapped_column(
        String(30), default="LEGACY_WEIGHTED", server_default="LEGACY_WEIGHTED"
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("play_sessions.id"), nullable=True
    )
    matchmaking_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    queue_groups_json: Mapped[list | None] = mapped_column(JSON, nullable=True)


class TeamRecord(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("match_id", "side"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    side: Mapped[str] = mapped_column(String(1))
    name: Mapped[str] = mapped_column(String(100))


class MatchPlayer(Base):
    __tablename__ = "match_players"
    __table_args__ = (
        UniqueConstraint("match_id", "player_id"),
        UniqueConstraint("match_id", "team", "position"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    team: Mapped[str] = mapped_column(String(1))
    position: Mapped[int] = mapped_column(Integer)


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (UniqueConstraint("match_id", "game_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    game_number: Mapped[int] = mapped_column(Integer)
    team_a_score: Mapped[int] = mapped_column(Integer)
    team_b_score: Mapped[int] = mapped_column(Integer)
    winner_team: Mapped[str | None] = mapped_column(String(1), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MatchEvent(Base):
    __tablename__ = "match_events"
    __table_args__ = (
        UniqueConstraint("event_id"),
        UniqueConstraint("match_id", "sequence_no"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36))
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id"), nullable=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(40))
    team: Mapped[str | None] = mapped_column(String(1), nullable=True)
    player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id"), nullable=True
    )
    payload_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Checkin(Base):
    __tablename__ = "player_checkins"
    session_id: Mapped[int | None] = mapped_column(ForeignKey("play_sessions.id"), nullable=True)
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    checkin_time: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    checkout_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="WAITING")


class QueueEntry(Base):
    __tablename__ = "player_queue"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), unique=True)
    checkin_id: Mapped[int] = mapped_column(ForeignKey("player_checkins.id"))
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("queue_groups.id"), nullable=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    position: Mapped[int] = mapped_column(Integer, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="WAITING")


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value_json: Mapped[dict] = mapped_column(JSON)


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[int] = mapped_column(primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DeviceSnapshot(Base):
    """Last known device state, including unregistered casual matches."""

    __tablename__ = "device_snapshots"
    device_id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditRecord(Base):
    __tablename__ = "operator_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(150))
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PhoneAccess(Base):
    __tablename__ = "phone_access"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    court_id: Mapped[int] = mapped_column(ForeignKey("courts.id"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QueueGroup(Base, AuditMixin):
    __tablename__ = "queue_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_type: Mapped[str] = mapped_column(String(10), default="SOLO")
    temporary: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="WAITING")


class PlaySession(Base, AuditMixin):
    __tablename__ = "play_sessions"
    event_type: Mapped[str] = mapped_column(String(20), default="OPEN_PLAY", server_default="OPEN_PLAY")
    event_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[int] = mapped_column(Integer, default=1)
    settings_json: Mapped[dict] = mapped_column(JSON)
    court_ids_json: Mapped[list] = mapped_column(JSON)


class SchedulerLock(Base):
    __tablename__ = "scheduler_lock"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class WinnerHold(Base):
    __tablename__ = "winner_holds"
    court_id: Mapped[int] = mapped_column(ForeignKey("courts.id"), primary_key=True)
    player_ids_json: Mapped[list] = mapped_column(JSON)
    streak: Mapped[int] = mapped_column(Integer, default=1)
    match_id: Mapped[int] = mapped_column(Integer)
    active: Mapped[int] = mapped_column(Integer, default=1)


class Tournament(Base, AuditMixin):
    __tablename__ = "tournaments"
    session_id: Mapped[int | None] = mapped_column(ForeignKey("play_sessions.id"), nullable=True)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    format: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="REGISTRATION")
    settings_json: Mapped[dict] = mapped_column(JSON)
    entries_json: Mapped[list] = mapped_column(JSON, default=list)
    fixtures_json: Mapped[list] = mapped_column(JSON, default=list)


class AnnouncementEvent(Base):
    """Browser-reported PA playback metadata; no audio is stored."""
    __tablename__ = "announcement_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    court_id: Mapped[int] = mapped_column(ForeignKey("courts.id"))
    actor: Mapped[str] = mapped_column(String(100))
    announcement_type: Mapped[str] = mapped_column(String(20))
    playback_status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str] = mapped_column(String(150), default="")
    announced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
