"""Initial durable execution schema, frozen before first release at revision 0001."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE executions (\n\tid UUID NOT NULL, \n\tbusiness_key VARCHAR(128) NOT NULL, \n\tidempotency_key VARCHAR(128) NOT NULL, \n\trequest_digest VARCHAR(64) NOT NULL, \n\tprompt TEXT NOT NULL, \n\tstate VARCHAR(32) NOT NULL, \n\tcorrelation_id UUID NOT NULL, \n\tmax_steps INTEGER NOT NULL, \n\tmax_tokens INTEGER NOT NULL, \n\tsteps INTEGER NOT NULL, \n\tmodel_calls INTEGER NOT NULL, \n\ttokens_used INTEGER NOT NULL, \n\tretry_count INTEGER NOT NULL, \n\tnext_attempt_at TIMESTAMP WITH TIME ZONE, \n\tdeadline_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlease_token UUID, \n\tlease_owner VARCHAR(128), \n\tlease_expires_at TIMESTAMP WITH TIME ZONE, \n\toutcome JSONB, \n\terror_code VARCHAR(80), \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT execution_state CHECK (state IN ('CREATED','RUNNING','WAITING_FOR_APPROVAL','TOOL_EXECUTING','RETRY_PENDING','RECONCILIATION_REQUIRED','SUCCEEDED','FAILED_PERMANENT','CANCELLED')), \n\tCONSTRAINT nonnegative_budgets CHECK (steps >= 0 AND model_calls >= 0 AND tokens_used >= 0 AND retry_count >= 0), \n\tCONSTRAINT complete_lease CHECK ((lease_token IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL) OR (lease_token IS NOT NULL AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)), \n\tCONSTRAINT terminal_outcome CHECK (state NOT IN ('SUCCEEDED','FAILED_PERMANENT','CANCELLED') OR (outcome IS NOT NULL AND jsonb_typeof(outcome) = 'object')), \n\tCONSTRAINT active_requires_lease CHECK (state NOT IN ('RUNNING','TOOL_EXECUTING') OR lease_token IS NOT NULL), \n\tUNIQUE (business_key), \n\tUNIQUE (idempotency_key)\n)"
    )
    op.execute("CREATE INDEX ix_execution_ready ON executions (state, next_attempt_at)")
    op.execute("CREATE INDEX ix_execution_stale ON executions (state, lease_expires_at)")
    op.execute(
        "CREATE TABLE webhook_receipts (\n\tnonce_hash VARCHAR(64) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (nonce_hash)\n)"
    )
    op.execute("CREATE INDEX ix_receipt_expiry ON webhook_receipts (expires_at)")
    op.execute(
        "CREATE TABLE tool_calls (\n\tid UUID NOT NULL, \n\texecution_id UUID NOT NULL, \n\tordinal INTEGER NOT NULL, \n\ttool VARCHAR(80) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\toperation_key VARCHAR(64) NOT NULL, \n\targuments JSONB NOT NULL, \n\tside_effect VARCHAR(24) NOT NULL, \n\tapproval_required BOOLEAN NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tattempt INTEGER NOT NULL, \n\toutcome JSONB, \n\texternal_id VARCHAR(128), \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_call_ordinal UNIQUE (execution_id, ordinal), \n\tCONSTRAINT uq_call_fingerprint UNIQUE (execution_id, fingerprint), \n\tCONSTRAINT call_status CHECK (status IN ('PROPOSED','DISPATCHED','SUCCEEDED','FAILED')), \n\tCONSTRAINT call_counters CHECK (ordinal > 0 AND attempt >= 0), \n\tCONSTRAINT successful_call_outcome CHECK (status != 'SUCCEEDED' OR (external_id IS NOT NULL AND outcome IS NOT NULL AND jsonb_typeof(outcome) = 'object')), \n\tFOREIGN KEY(execution_id) REFERENCES executions (id) ON DELETE CASCADE, \n\tUNIQUE (operation_key)\n)"
    )
    op.execute(
        "CREATE TABLE approvals (\n\tcall_id UUID NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tdecision BOOLEAN, \n\tactor VARCHAR(80), \n\tdecided_at TIMESTAMP WITH TIME ZONE, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (call_id), \n\tCONSTRAINT complete_approval_decision CHECK ((decision IS NULL AND actor IS NULL AND decided_at IS NULL) OR (decision IS NOT NULL AND actor IS NOT NULL AND decided_at IS NOT NULL)), \n\tFOREIGN KEY(call_id) REFERENCES tool_calls (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE TABLE execution_events (\n\tid BIGSERIAL NOT NULL, \n\texecution_id UUID NOT NULL, \n\ttimestamp TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tkind VARCHAR(80) NOT NULL, \n\tfrom_state VARCHAR(32), \n\tto_state VARCHAR(32) NOT NULL, \n\ttool_call_id UUID, \n\tdetails JSONB NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(execution_id) REFERENCES executions (id) ON DELETE CASCADE, \n\tFOREIGN KEY(tool_call_id) REFERENCES tool_calls (id) ON DELETE SET NULL\n)"
    )
    op.execute("CREATE INDEX ix_event_history ON execution_events (execution_id, id)")


def downgrade() -> None:
    op.drop_table("execution_events")
    op.drop_table("approvals")
    op.drop_table("tool_calls")
    op.drop_table("webhook_receipts")
    op.drop_table("executions")
