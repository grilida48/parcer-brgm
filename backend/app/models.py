from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, Numeric, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, connect_args={'check_same_thread': False} if settings.database_url.startswith('sqlite') else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default='staff')
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Lead(Base):
    __tablename__ = 'leads'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(200), index=True)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(100), index=True)
    website: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    socials: Mapped[list] = mapped_column(JSON, default=list)
    email: Mapped[str | None] = mapped_column(String(320))
    card_url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default='manual')
    source_id: Mapped[str | None] = mapped_column(String(200))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    website_status: Mapped[str] = mapped_column(String(30), default='not_listed')
    audit: Mapped[dict] = mapped_column(JSON, default=dict)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    score_reasons: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(100), default='Новый', index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), index=True)
    notes: Mapped[str] = mapped_column(Text, default='')
    order_value: Mapped[float] = mapped_column(Numeric(14, 2), default=15000)
    next_contact: Mapped[str | None] = mapped_column(String(40))
    consent: Mapped[str] = mapped_column(String(20), default='unknown')
    version: Mapped[int] = mapped_column(Integer, default=1)
    export_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    is_chain: Mapped[bool | None] = mapped_column(Boolean)
    search_id: Mapped[int | None] = mapped_column(ForeignKey('searches.id'))
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)


class Search(Base):
    __tablename__ = 'searches'
    id: Mapped[int] = mapped_column(primary_key=True)
    criteria: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default='queued')
    progress: Mapped[int] = mapped_column(Integer, default=0)
    found: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class SearchLead(Base):
    __tablename__ = 'search_leads'
    search_id: Mapped[int] = mapped_column(ForeignKey('searches.id'), primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey('leads.id'), primary_key=True)


class Interaction(Base):
    __tablename__ = 'interactions'
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey('leads.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AppSetting(Base):
    __tablename__ = 'settings'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class Activity(Base):
    __tablename__ = 'activities'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    action: Mapped[str] = mapped_column(String(200))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
