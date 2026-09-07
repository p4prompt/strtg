from datetime import datetime
from typing import List

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    total_size: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )
    total_parts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="ready",
        nullable=False,
    )
    download_token: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    parts: Mapped[List["FilePart"]] = relationship(
        "FilePart",
        back_populates="file",
        cascade="all, delete-orphan",
        order_by="FilePart.part_number",
        lazy="selectin",
    )


class FilePart(Base):
    __tablename__ = "file_parts"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    part_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    telegram_message_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(
        Text,
        nullable=True,
    )
    size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    file: Mapped["File"] = relationship(
        "File",
        back_populates="parts",
    )

    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "part_number",
            name="uq_file_part_order",
        ),
    )
