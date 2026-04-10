"""Initial schema."""

import sqlalchemy as sa
from alembic import op

revision = "20260408_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insiders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reporting_owner_cik", sa.String(length=20), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_director", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_officer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_ten_percent_owner", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("officer_title", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_insiders")),
    )
    op.create_index(op.f("ix_insiders_name"), "insiders", ["name"], unique=False)
    op.create_index(
        op.f("ix_insiders_reporting_owner_cik"), "insiders", ["reporting_owner_cik"], unique=False
    )

    op.create_table(
        "issuers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cik", sa.String(length=20), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("exchange", sa.String(length=50), nullable=True),
        sa.Column("sic", sa.String(length=20), nullable=True),
        sa.Column("state_of_incorp", sa.String(length=20), nullable=True),
        sa.Column("market_cap", sa.Numeric(20, 2), nullable=True),
        sa.Column("latest_price", sa.Numeric(20, 4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issuers")),
        sa.UniqueConstraint("cik", name=op.f("uq_issuers_cik")),
    )
    op.create_index(op.f("ix_issuers_cik"), "issuers", ["cik"], unique=False)
    op.create_index(op.f("ix_issuers_ticker"), "issuers", ["ticker"], unique=False)

    op.create_table(
        "filings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("accession_number", sa.String(length=32), nullable=False),
        sa.Column("form_type", sa.String(length=10), nullable=False),
        sa.Column("issuer_id", sa.Integer(), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("xml_url", sa.Text(), nullable=False),
        sa.Column("is_amendment", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw_xml_path", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("extra_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["issuer_id"], ["issuers.id"], name=op.f("fk_filings_issuer_id_issuers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filings")),
        sa.UniqueConstraint("accession_number", name=op.f("uq_filings_accession_number")),
    )
    op.create_index(
        op.f("ix_filings_accession_number"), "filings", ["accession_number"], unique=False
    )
    op.create_index(op.f("ix_filings_fingerprint"), "filings", ["fingerprint"], unique=False)
    op.create_index(op.f("ix_filings_form_type"), "filings", ["form_type"], unique=False)

    op.create_table(
        "signal_windows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issuer_id", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("unique_buyers", sa.Integer(), nullable=False),
        sa.Column("total_purchase_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("average_purchase_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("signal_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("health_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("price_context_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("summary_status", sa.String(length=50), nullable=False),
        sa.Column("rationale_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["issuer_id"], ["issuers.id"], name=op.f("fk_signal_windows_issuer_id_issuers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_windows")),
    )
    op.create_index(
        op.f("ix_signal_windows_issuer_id"), "signal_windows", ["issuer_id"], unique=False
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_window_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["signal_window_id"],
            ["signal_windows.id"],
            name=op.f("fk_alerts_signal_window_id_signal_windows"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
    )
    op.create_index(
        op.f("ix_alerts_signal_window_id"), "alerts", ["signal_window_id"], unique=False
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("insider_id", sa.Integer(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=True),
        sa.Column("security_title", sa.String(length=255), nullable=True),
        sa.Column("is_derivative", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("transaction_code", sa.String(length=10), nullable=True),
        sa.Column("acquired_disposed", sa.String(length=1), nullable=True),
        sa.Column("shares", sa.Numeric(20, 4), nullable=True),
        sa.Column("price_per_share", sa.Numeric(20, 4), nullable=True),
        sa.Column("value_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("shares_after", sa.Numeric(20, 4), nullable=True),
        sa.Column("ownership_type", sa.String(length=1), nullable=True),
        sa.Column("deemed_execution_date", sa.Date(), nullable=True),
        sa.Column("footnote_text", sa.Text(), nullable=True),
        sa.Column("is_candidate_buy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_likely_routine", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("routine_reason", sa.String(length=255), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"], ["filings.id"], name=op.f("fk_transactions_filing_id_filings")
        ),
        sa.ForeignKeyConstraint(
            ["insider_id"], ["insiders.id"], name=op.f("fk_transactions_insider_id_insiders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transactions")),
    )
    op.create_index(op.f("ix_transactions_filing_id"), "transactions", ["filing_id"], unique=False)
    op.create_index(
        op.f("ix_transactions_insider_id"), "transactions", ["insider_id"], unique=False
    )
    op.create_index(
        op.f("ix_transactions_transaction_code"), "transactions", ["transaction_code"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_transactions_transaction_code"), table_name="transactions")
    op.drop_index(op.f("ix_transactions_insider_id"), table_name="transactions")
    op.drop_index(op.f("ix_transactions_filing_id"), table_name="transactions")
    op.drop_table("transactions")
    op.drop_index(op.f("ix_alerts_signal_window_id"), table_name="alerts")
    op.drop_table("alerts")
    op.drop_index(op.f("ix_signal_windows_issuer_id"), table_name="signal_windows")
    op.drop_table("signal_windows")
    op.drop_index(op.f("ix_filings_form_type"), table_name="filings")
    op.drop_index(op.f("ix_filings_fingerprint"), table_name="filings")
    op.drop_index(op.f("ix_filings_accession_number"), table_name="filings")
    op.drop_table("filings")
    op.drop_index(op.f("ix_issuers_ticker"), table_name="issuers")
    op.drop_index(op.f("ix_issuers_cik"), table_name="issuers")
    op.drop_table("issuers")
    op.drop_index(op.f("ix_insiders_reporting_owner_cik"), table_name="insiders")
    op.drop_index(op.f("ix_insiders_name"), table_name="insiders")
    op.drop_table("insiders")
