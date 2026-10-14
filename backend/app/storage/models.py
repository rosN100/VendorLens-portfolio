"""File metadata, not document/case identity (those arrive with intake)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class StoredFile(Base):
    __tablename__ = "stored_files"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_stored_files_request_id"),
        CheckConstraint("byte_size >= 0", name="ck_stored_files_size"),
        CheckConstraint("checksum_algorithm = 'sha256'", name="ck_stored_files_algorithm"),
    )

    file_id: Mapped[UUID] = mapped_column(primary_key=True)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_algorithm: Mapped[str] = mapped_column(String(16), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
