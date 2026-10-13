# Court switching verification — 2026-10-06

Implemented and deployed guided host-admin court switching, durable jobs with restart recovery, an independent low-priority worker, and restricted management agents on both existing Pis. The current Court 1/Dink With Grace and Court 2/Rally Point bindings were preserved.

Validation:
- Full regression suite: 173 passed, 1 optional test skipped before final durability improvements.
- Final transfer/agent tests: 13 passed locally, including partially completed source preparation, idempotency, rollback, recovery retry, access control, unfinished-game guards and shutdown races.
- Pi transfer tests: 12 passed before the additional partial-preparation parameter case; compile/import checks passed after final deployment.
- Browser test in an isolated fake-device environment: review/confirmation, progress, COMPLETE, destination navigation and final page rendering verified.
- Actual Court 1 agent applied/reconnected/finalized its existing Dink With Grace configuration without changing assignment.
- Actual restricted Court 2 SSH management transport responded successfully. Both Pis remain connected with GPIO ONLINE. Court 2's cross-club move was reviewed without executing it.
- Host services web, scoreboard and court-manager are active.
- Legacy database counts remain 9 players, 10 matches, 2 staff, 2 devices.

Backups:
- Before feature: /opt/rallypoint/backups/automatic/rallypoint-20261006-030141-365834
- Court 2 source/state/config: /opt/rallypoint/backups/before-court-manager-20261006-112107/state-and-source.tar.gz
- Final host backup, checksums and database restore verified: /opt/rallypoint/backups/automatic/rallypoint-20261006-033309-273452

No production players, clubs or matches were created for testing. No RF scoring commands were sent during deployment verification. Future physical moves occur only after a host administrator confirms the reviewed move.
