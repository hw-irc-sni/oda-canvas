"""SQLAlchemy models. Ids are TMF string ids; timestamps are timezone-aware."""
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text,
                        func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

BLOB_CHUNK_SIZE = 1024 * 1024  # document content is stored in 1 MiB rows


class Base(DeclarativeBase):
    pass


def created_at():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def updated_at():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Knowledge(Base):
    """A knowledge-base. `body` is the resource as served; the columns are indexed copies for filtering."""
    __tablename__ = "knowledge"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    store_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created: Mapped[datetime] = created_at()
    updated: Mapped[datetime] = updated_at()


class KnowledgeSpecification(Base):
    """An ontology module."""
    __tablename__ = "knowledge_specification"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    iri: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    spec_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    layer: Mapped[str | None] = mapped_column(String(16))
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    link_status: Mapped[str | None] = mapped_column(String(16))
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created: Mapped[datetime] = created_at()
    updated: Mapped[datetime] = updated_at()


class Mapping(Base):
    """A bridge statement (reified bridge:mapping) of a bridge module."""
    __tablename__ = "mapping"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    specification_id: Mapped[str] = mapped_column(
        Text, ForeignKey("knowledge_specification.id", ondelete="CASCADE"), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    object: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str | None] = mapped_column(String(8))
    rationale: Mapped[str | None] = mapped_column(Text)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created: Mapped[datetime] = created_at()
    updated: Mapped[datetime] = updated_at()


class Document(Base):
    """Metadata of a document uploaded to a knowledge-base; its content is in DocumentBlob rows."""
    __tablename__ = "document"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    knowledge_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False,
                                              index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ingestion_state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    last_process_id: Mapped[str | None] = mapped_column(Text)
    upload_date: Mapped[datetime] = created_at()


class DocumentBlob(Base):
    """One chunk (up to BLOB_CHUNK_SIZE bytes) of a document's content."""
    __tablename__ = "document_blob"
    document_id: Mapped[str] = mapped_column(Text, ForeignKey("document.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class Process(Base):
    """A TMF701 v5 process: one ingestion run."""
    __tablename__ = "process"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    knowledge_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False,
                                              index=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    request: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created: Mapped[datetime] = created_at()
    started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Task(Base):
    """A TMF701 v5 task: one KM-FX step of a process, claimed by a worker with SELECT ... FOR UPDATE SKIP LOCKED."""
    __tablename__ = "task"
    __table_args__ = (Index("ix_task_claim", "state", "created"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    process_id: Mapped[str] = mapped_column(Text, ForeignKey("process.id", ondelete="CASCADE"), nullable=False,
                                            index=True)
    step: Mapped[str] = mapped_column(String(16), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created: Mapped[datetime] = created_at()
    updated: Mapped[datetime] = updated_at()


class ReviewItem(Base):
    """A proposal, review comment or ruling on a module or one of its mappings."""
    __tablename__ = "review_item"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    specification_id: Mapped[str] = mapped_column(
        Text, ForeignKey("knowledge_specification.id", ondelete="CASCADE"), nullable=False, index=True)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    ruling: Mapped[str | None] = mapped_column(String(16))
    link_status: Mapped[str | None] = mapped_column(String(16))
    author: Mapped[str] = mapped_column(Text, nullable=False)
    approver: Mapped[str | None] = mapped_column(Text)
    created: Mapped[datetime] = created_at()


class StateTransition(Base):
    """The audit trail of lifecycle transitions of knowledge-bases, modules and mappings."""
    __tablename__ = "state_transition"
    __table_args__ = (Index("ix_state_transition_target", "target_kind", "target_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    review_item_id: Mapped[str | None] = mapped_column(Text, ForeignKey("review_item.id", ondelete="SET NULL"))
    at: Mapped[datetime] = created_at()


class HubSubscription(Base):
    """An event subscription registered through /hub."""
    __tablename__ = "hub_subscription"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    callback: Mapped[str] = mapped_column(Text, nullable=False)
    query: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(Text)
    created: Mapped[datetime] = created_at()


class RecommendFeedback(Base):
    """Feedback on a recommended item (KM-FX-06 ranking signals)."""
    __tablename__ = "recommend_feedback"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    knowledge_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False,
                                              index=True)
    recommendation_id: Mapped[str] = mapped_column(Text, nullable=False)
    item_id: Mapped[str] = mapped_column(Text, nullable=False)
    used: Mapped[bool | None] = mapped_column(Boolean)
    outcome: Mapped[str | None] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    created: Mapped[datetime] = created_at()


class ValidationReport(Base):
    """The latest validation report of an ontology module."""
    __tablename__ = "validation_report"
    specification_id: Mapped[str] = mapped_column(
        Text, ForeignKey("knowledge_specification.id", ondelete="CASCADE"), primary_key=True)
    report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    run_at: Mapped[datetime] = created_at()
