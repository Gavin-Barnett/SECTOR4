"""Add insider compensation records."""

import sqlalchemy as sa
from alembic import op

revision = "20260409_0003"
down_revision = "20260408_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insider_compensation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issuer_id", sa.Integer(), nullable=False),
        sa.Column("insider_id", sa.Integer(), nullable=True),
        sa.Column("insider_name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("salary_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("bonus_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("stock_awards_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("option_awards_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("non_equity_incentive_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("all_other_comp_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("total_compensation_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("source_accession_number", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["insider_id"],
            ["insiders.id"],
            name=op.f("fk_insider_compensation_insider_id_insiders"),
        ),
        sa.ForeignKeyConstraint(
            ["issuer_id"], ["issuers.id"], name=op.f("fk_insider_compensation_issuer_id_issuers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_insider_compensation")),
        sa.UniqueConstraint(
            "issuer_id",
            "insider_name",
            "fiscal_year",
            "source_accession_number",
            name="uq_insider_compensation_record",
        ),
    )
    op.create_index(
        op.f("ix_insider_compensation_insider_id"),
        "insider_compensation",
        ["insider_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_insider_compensation_insider_name"),
        "insider_compensation",
        ["insider_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_insider_compensation_issuer_id"),
        "insider_compensation",
        ["issuer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_insider_compensation_source_accession_number"),
        "insider_compensation",
        ["source_accession_number"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_insider_compensation_source_accession_number"),
        table_name="insider_compensation",
    )
    op.drop_index(op.f("ix_insider_compensation_issuer_id"), table_name="insider_compensation")
    op.drop_index(op.f("ix_insider_compensation_insider_name"), table_name="insider_compensation")
    op.drop_index(op.f("ix_insider_compensation_insider_id"), table_name="insider_compensation")
    op.drop_table("insider_compensation")
