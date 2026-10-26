"""The gateway's own data: people, keys, upstream registrations, tool catalog, audit.

Store data (products, orders) never lands here. It stays in WooCommerce and
passes through the gateway only as tool results.

DATABASE_URL picks the engine. Local development and the cluster both use
SQLite (in the cluster, on a persistent volume). aiomysql is installed for
MariaDB, but that path is untested.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Connection, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    # Stored as naive UTC so SQLite and MariaDB behave the same.
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(16))  # admin | member
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped[User] = relationship(lazy="joined")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(32))  # shown in the UI and audit log
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="joined")


class Server(Base):
    """An upstream MCP server registered by an admin."""

    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)  # tool-name prefix, e.g. "woo"
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_type: Mapped[str] = mapped_column(String(16), default="none")  # none | bearer | header
    auth_header: Mapped[str | None] = mapped_column(String(100), nullable=True)
    credential_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet ciphertext
    timeout_seconds: Mapped[float] = mapped_column(Float, default=10.0)
    # Off by default: when on, each call carries the caller's display name (never the email),
    # so the upstream can show who asked. Only for upstreams the team runs or trusts.
    forward_identity: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # "provisioner" for a store's MCP server that the gateway registered by itself;
    # it is removed with its store. None for servers an admin added.
    managed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Health, as last observed by discovery or a call.
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # unknown | ok | down | auth_failed
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protocol_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    tools: Mapped[list[Tool]] = relationship(back_populates="server", cascade="all, delete-orphan", passive_deletes=True)


class Tool(Base):
    """One upstream tool as last discovered, plus the admin's access rule for it."""

    __tablename__ = "tools"
    __table_args__ = (UniqueConstraint("server_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    annotations: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Roles allowed to see and call this tool. Defaults from the read-only hint at
    # first discovery; after that only an admin changes it.
    allowed_roles: Mapped[list[str]] = mapped_column(JSON)
    # False when the upstream stopped advertising it. Kept so the admin's rule survives.
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    server: Mapped[Server] = relationship(back_populates="tools")


class AuditEvent(Base):
    """One tool call attempt through the gateway, successful or not."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    # Copied, not only referenced, so the record stays readable after a user or
    # server is deleted.
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_email: Mapped[str] = mapped_column(String(255))
    via: Mapped[str] = mapped_column(String(16))  # mcp | chat
    api_key_prefix: Mapped[str | None] = mapped_column(String(32), nullable=True)
    server_slug: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    public_name: Mapped[str] = mapped_column(String(200))
    arguments: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON, truncated
    outcome: Mapped[str] = mapped_column(String(24), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


def add_missing_columns(conn: Connection) -> None:
    """Add columns that a newer version of a model has and an older database lacks.

    `create_all` makes missing tables but never alters existing ones, and this project has no
    migration tool. Only plain additive columns with a default are handled here, which is all
    that has changed so far; anything bigger needs a real migration.
    """
    inspector = inspect(conn)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            if column.server_default is None and not column.nullable:
                raise RuntimeError(f"{table.name}.{column.name} is new and NOT NULL without a default: write a migration")
            ddl = column.type.compile(conn.dialect)
            default = f" DEFAULT {column.server_default.arg}" if column.server_default is not None else ""
            conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl}{default}"))


class Database:
    def __init__(self, url: str):
        kwargs: dict[str, Any] = {}
        if url.startswith("mysql"):
            # MariaDB closes idle connections; check before use and recycle hourly.
            kwargs = {"pool_pre_ping": True, "pool_recycle": 3600}
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(add_missing_columns)

    def session(self) -> AsyncSession:
        return self.sessions()

    async def dispose(self) -> None:
        await self.engine.dispose()
