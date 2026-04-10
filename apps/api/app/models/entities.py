from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Issuer(TimestampMixin, Base):
    __tablename__ = "issuers"

    id: Mapped[int] = mapped_column(primary_key=True)
    cik: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    ticker: Mapped[str | None] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(255))
    exchange: Mapped[str | None] = mapped_column(String(50))
    sic: Mapped[str | None] = mapped_column(String(20))
    state_of_incorp: Mapped[str | None] = mapped_column(String(20))
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    latest_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))

    filings: Mapped[list[Filing]] = relationship(
        back_populates="issuer", cascade="all, delete-orphan"
    )
    compensation_records: Mapped[list[InsiderCompensation]] = relationship(
        back_populates="issuer", cascade="all, delete-orphan"
    )
    signal_windows: Mapped[list[SignalWindow]] = relationship(back_populates="issuer")


class Insider(TimestampMixin, Base):
    __tablename__ = "insiders"

    id: Mapped[int] = mapped_column(primary_key=True)
    reporting_owner_cik: Mapped[str | None] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    is_director: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_officer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_ten_percent_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    officer_title: Mapped[str | None] = mapped_column(String(255))

    transactions: Mapped[list[Transaction]] = relationship(back_populates="insider")
    compensation_records: Mapped[list[InsiderCompensation]] = relationship(back_populates="insider")


class Filing(TimestampMixin, Base):
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    accession_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    form_type: Mapped[str] = mapped_column(String(10), index=True)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("issuers.id"), nullable=False)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    xml_url: Mapped[str] = mapped_column(Text, nullable=False)
    is_amendment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_xml_path: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    issuer: Mapped[Issuer] = relationship(back_populates="filings")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="filing", cascade="all, delete-orphan"
    )


class InsiderCompensation(TimestampMixin, Base):
    __tablename__ = "insider_compensation"
    __table_args__ = (
        UniqueConstraint(
            "issuer_id",
            "insider_name",
            "fiscal_year",
            "source_accession_number",
            name="uq_insider_compensation_record",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("issuers.id"), nullable=False, index=True)
    insider_id: Mapped[int | None] = mapped_column(ForeignKey("insiders.id"), index=True)
    insider_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    salary_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    bonus_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    stock_awards_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    option_awards_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    non_equity_incentive_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    all_other_comp_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    total_compensation_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    source_accession_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    issuer: Mapped[Issuer] = relationship(back_populates="compensation_records")
    insider: Mapped[Insider | None] = relationship(back_populates="compensation_records")


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), nullable=False, index=True)
    insider_id: Mapped[int] = mapped_column(ForeignKey("insiders.id"), nullable=False, index=True)
    transaction_date: Mapped[date | None] = mapped_column(Date)
    security_title: Mapped[str | None] = mapped_column(String(255))
    is_derivative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    transaction_code: Mapped[str | None] = mapped_column(String(10), index=True)
    acquired_disposed: Mapped[str | None] = mapped_column(String(1))
    shares: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    price_per_share: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    value_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    shares_after: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    ownership_type: Mapped[str | None] = mapped_column(String(1))
    deemed_execution_date: Mapped[date | None] = mapped_column(Date)
    footnote_text: Mapped[str | None] = mapped_column(Text)
    is_candidate_buy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_likely_routine: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    routine_reason: Mapped[str | None] = mapped_column(String(255))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    filing: Mapped[Filing] = relationship(back_populates="transactions")
    insider: Mapped[Insider] = relationship(back_populates="transactions")


class SignalWindow(TimestampMixin, Base):
    __tablename__ = "signal_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("issuers.id"), nullable=False, index=True)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    unique_buyers: Mapped[int] = mapped_column(Integer, nullable=False)
    total_purchase_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    average_purchase_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    signal_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    health_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    price_context_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    summary_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    rationale_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    issuer: Mapped[Issuer] = relationship(back_populates="signal_windows")
    alerts: Mapped[list[Alert]] = relationship(back_populates="signal_window")


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_window_id: Mapped[int] = mapped_column(
        ForeignKey("signal_windows.id"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    signal_window: Mapped[SignalWindow] = relationship(back_populates="alerts")
