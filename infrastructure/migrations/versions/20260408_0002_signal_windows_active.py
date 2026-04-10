"""Add active flag to signal windows."""

import sqlalchemy as sa
from alembic import op

revision = "20260408_0002"
down_revision = "20260408_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signal_windows",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index(
        op.f("ix_signal_windows_is_active"), "signal_windows", ["is_active"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_signal_windows_is_active"), table_name="signal_windows")
    op.drop_column("signal_windows", "is_active")
