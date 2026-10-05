"""Tenant admission identities and a bounded reconciliation ledger; 0001 stays frozen."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tool_calls", sa.Column("provider_binding", sa.String(256), nullable=True))
    op.add_column(
        "executions", sa.Column("tenant_id", sa.String(64), server_default="legacy", nullable=False)
    )
    op.drop_constraint("executions_business_key_key", "executions", type_="unique")
    op.drop_constraint("executions_idempotency_key_key", "executions", type_="unique")
    op.create_unique_constraint("uq_tenant_business", "executions", ["tenant_id", "business_key"])
    op.create_unique_constraint(
        "uq_tenant_idempotency", "executions", ["tenant_id", "idempotency_key"]
    )
    op.create_check_constraint(
        "valid_tenant_id", "executions", "tenant_id ~ '^[A-Za-z0-9_.-]{1,64}$'"
    )
    op.create_index("ix_execution_tenant_created", "executions", ["tenant_id", "created_at"])
    op.add_column(
        "webhook_receipts",
        sa.Column("tenant_id", sa.String(64), server_default="legacy", nullable=False),
    )
    op.drop_constraint("webhook_receipts_pkey", "webhook_receipts", type_="primary")
    op.create_primary_key("webhook_receipts_pkey", "webhook_receipts", ["tenant_id", "nonce_hash"])
    op.create_table(
        "reconciliations",
        sa.Column(
            "call_id",
            sa.Uuid(),
            sa.ForeignKey("tool_calls.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("budget", sa.Integer(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('AMBIGUOUS','RECONCILING','MANUAL_REVIEW','RESOLVED','ABANDONED')",
            name="reconciliation_status",
        ),
        sa.CheckConstraint("attempts >= 0 AND budget > 0", name="reconciliation_budget"),
    )
    op.create_index("ix_reconciliation_due", "reconciliations", ["status", "next_attempt_at"])
    # Legacy uncertain writes retain their original operation keys and dispatch state.
    op.execute("""
        INSERT INTO reconciliations (call_id,status,attempts,budget,deadline_at,next_attempt_at)
        SELECT c.id,'AMBIGUOUS',0,3,clock_timestamp()+interval '1 hour',clock_timestamp()
        FROM tool_calls c JOIN executions e ON c.execution_id=e.id
        WHERE e.state='RECONCILIATION_REQUIRED' AND c.status='DISPATCHED'
    """)


def downgrade() -> None:
    # Refuse a rollback that would erase isolation, decisions, or uncertain-effect evidence.
    # Validated on empty/legacy-only databases; never flatten multiple tenants into v1.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM executions WHERE tenant_id <> 'legacy')
             OR EXISTS (SELECT 1 FROM webhook_receipts WHERE tenant_id <> 'legacy')
             OR EXISTS (SELECT 1 FROM reconciliations)
             OR EXISTS (SELECT 1 FROM tool_calls WHERE provider_binding IS NOT NULL) THEN
            RAISE EXCEPTION 'unsafe v2 downgrade: preserve tenant and reconciliation evidence';
          END IF;
        END $$
    """)
    op.drop_table("reconciliations")
    op.drop_column("tool_calls", "provider_binding")
    op.drop_constraint("webhook_receipts_pkey", "webhook_receipts", type_="primary")
    op.drop_column("webhook_receipts", "tenant_id")
    op.create_primary_key("webhook_receipts_pkey", "webhook_receipts", ["nonce_hash"])
    op.drop_index("ix_execution_tenant_created", table_name="executions")
    op.drop_constraint("valid_tenant_id", "executions", type_="check")
    op.drop_constraint("uq_tenant_business", "executions", type_="unique")
    op.drop_constraint("uq_tenant_idempotency", "executions", type_="unique")
    op.drop_column("executions", "tenant_id")
    op.create_unique_constraint("executions_business_key_key", "executions", ["business_key"])
    op.create_unique_constraint("executions_idempotency_key_key", "executions", ["idempotency_key"])
