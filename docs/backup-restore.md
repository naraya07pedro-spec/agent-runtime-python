# Backup, interruption and restore procedure

Use PostgreSQL 17-compatible client utilities and injected PG connection configuration or
a managed passfile. Do not place credentials in CLI URLs, logs, Git, or chat. A dump contains
prompts/results and business data: restrict permissions, encrypt at rest, apply retention,
and verify off-host storage in the real deployment. Those deployment controls are not
certified by this reference. Roles/tablespaces are not in this single-database dump.

`bash scripts/database_backup.sh /protected/new-snapshot.dump` runs a consistent custom-format
pg_dump, with no owner/ACL restoration. It refuses to overwrite a file. For an isolated
empty database named `*_restore_test`, set `RUNTIME_RESTORE_ACK=isolated-empty-database`
and run `bash scripts/database_restore.sh /protected/new-snapshot.dump`. The wrapper rejects
nonempty/non-isolated targets and uses a single transaction with exit-on-error. It does not
drop or replace an existing database. Promotion of a restored database is an operator action
outside this automated drill and requires its own change review.

Before restoration/promotion: stop all workers and admission, set
`RUNTIME_RECOVERY_READ_ONLY=true`, and ensure old deployment processes cannot reconnect.
This mode rejects new admission/claims, makes readiness unavailable, and allows safe recovery
and provider lookups. It is a required operator setting, not automatic restore detection.

Validate migration revision/drift, table/FK/check constraints, event/action identities,
pending approvals, tenant configuration and external provider bindings. Preserve the full
dump and operation history privately. Restore tenant/key configuration from its separately
managed source; it is deliberately not stored as credentials in runtime tables.

Compare the snapshot time with the outage and independent provider history. Effects after
the snapshot may have no restored dispatch intent, or a snapshot can say CREATED for a job
that later sent a write. This runtime cannot discover every such missing operation. Do not
resume dispatch until the loss window has been investigated; a new business key is not safe
recovery. Already restored DISPATCHED writes can be fenced and reconciled read-only. Record
unknown effects explicitly; operators may abandon scheduling without claiming cancellation.

The [CI drill](../scripts/recovery_drill.py) only accepts an explicitly opted-in disposable
`runtime_test` container. It uses both wrappers, SIGKILLs PostgreSQL during a flushed
uncommitted transaction, observes rejection during outage, reconnects the same pool, checks
rollback, restores to an empty database and compares row counts/content signatures. The
signatures detect accidental row differences, not malicious tampering. A restored worker
claim is blocked; a pre-restore lease is rejected; a persisted dispatch is reconciled against
the ORIGINAL sandbox provider ledger with no POST. The restore's copied sandbox table is
not treated as independent external evidence. Dumps are temporary and never uploaded.

Machine results are in CI artifact `recovery-drill.json`, with source SHA, timings and flags.
Times measure this one controlled small-database drill. RPO is determined by snapshot/WAL
coverage and effect history; RTO includes provision, restore, validation, credential/tenant
reconfiguration and external review. No numeric production RPO/RTO, HA, PITR, multi-region
recovery, disk-corruption tolerance or full disaster-recovery guarantee is claimed.
