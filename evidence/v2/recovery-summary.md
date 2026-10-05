# Disposable database recovery drill

Generated 2026-10-05T04:08:44.593250+00:00 on Linux-6.17.0-1022-azure-x86_64-with-glibc2.39, Python 3.12.14.

Tested commit: `833a4eff040bea8b952e84d9b5207c2335c036c2`. Source commit: `833a4eff040bea8b952e84d9b5207c2335c036c2`.

Status: passed. Synthetic sandbox only; no production RPO/RTO guarantee.

Backup: 0.1245 s; reconnect after SIGKILL/start: 0.631 s; restore/integrity/reconciliation: 0.2352 s.

Verified: interrupted transaction rejection and rollback, unavailable-state rejection, snapshot row integrity, blocked restored claims, stale pre-restore lease rejection, one original-provider effect and zero post-restore POSTs.
