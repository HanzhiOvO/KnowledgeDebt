"""SQLAlchemy metadata for migrations and the gradual repository transition.

The current service keeps its compact SQL repository API so SQLite users can upgrade
without a data rewrite. New production deployments share these ORM definitions with
Alembic, while repositories can move to typed sessions one aggregate at a time.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CourseORM(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    semester: Mapped[str] = mapped_column(Text)
    teacher: Mapped[str | None] = mapped_column(Text)
    schedule: Mapped[str | None] = mapped_column(Text)
    profile_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class SessionORM(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[str | None] = mapped_column(Text)
    ends_at: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text)
    reconstruction_score: Mapped[int] = mapped_column(Integer, default=0)
    learning_coverage: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(Text, default="open")
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class ResourceORM(Base):
    __tablename__ = "resources"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)
    evidence_level: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    storage_provider: Mapped[str] = mapped_column(Text, default="local")
    storage_key: Mapped[str | None] = mapped_column(Text)
    external_url: Mapped[str | None] = mapped_column(Text)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    coverage: Mapped[float] = mapped_column(Float, default=1)
    quality: Mapped[float] = mapped_column(Float, default=1)
    relevance: Mapped[float] = mapped_column(Float, default=1)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    start_offset: Mapped[float | None] = mapped_column(Float)
    end_offset: Mapped[float | None] = mapped_column(Float)
    session_duration: Mapped[float | None] = mapped_column(Float)
    capture_range_json: Mapped[str] = mapped_column(Text, default="[]")
    upload_state: Mapped[str] = mapped_column(Text, default="local_only")
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class KnowledgePointORM(Base):
    __tablename__ = "knowledge_points"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    source_session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    prerequisites_json: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer)
    expected_mastery: Mapped[int] = mapped_column(Integer)
    current_mastery: Mapped[float] = mapped_column(Float, default=0)
    confidence: Mapped[str] = mapped_column(Text)
    sources_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class DebtORM(Base):
    __tablename__ = "debts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    knowledge_point_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE"), unique=True
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    current_mastery: Mapped[float] = mapped_column(Float)
    target_mastery: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer)
    estimated_minutes: Mapped[int] = mapped_column(Integer)
    blocks_next_session: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(Text)


class JobORM(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    resource_id: Mapped[str | None] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)
