"""Add signal outcome checkpoints."""

import sqlalchemy as sa
from alembic import op

revision = "20260410_0004"
down_revision = "20260409_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signal_outcome_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_window_id", sa.Integer(), nullable=False),
        sa.Column("checkpoint_label", sa.String(length=20), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("price_date", sa.Date(), nullable=True),
        sa.Column("price_value", sa.Numeric(20, 4), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["signal_window_id"],
            ["signal_windows.id"],
            name=op.f("fk_signal_outcome_checkpoints_signal_window_id_signal_windows"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_outcome_checkpoints")),
        sa.UniqueConstraint(
            "signal_window_id",
            "checkpoint_label",
            name="uq_signal_outcome_checkpoint_label",
        ),
    )
    op.create_index(
        op.f("ix_signal_outcome_checkpoints_signal_window_id"),
        "signal_outcome_checkpoints",
        ["signal_window_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_signal_outcome_checkpoints_signal_window_id"),
        table_name="signal_outcome_checkpoints",
    )
    op.drop_table("signal_outcome_checkpoints")
