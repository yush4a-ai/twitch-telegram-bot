# R3 Commercial Growth Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure and remove avoidable SQLite sample duplication and Telegram fan-out coupling while preserving existing notifications and reports on staging.

**Architecture:** A local synthetic harness establishes baseline latency. Additive SQLite tables store shared stream observations and a durable notification job queue. A bounded worker drains jobs independently of Twitch polling; legacy samples and current sender remain available during staging cutover.

**Tech Stack:** Python 3.12, aiosqlite, aiohttp/aiogram, SQLite WAL, pytest; Railway staging guard and backup tool from R1.

**Spec:** `docs/superpowers/specs/2026-09-30-r3-growth-foundation-design.md`

## Global Constraints

- Only local/staging and `@TwitchSignalTestbot`; no production deploy, variables, DB, data or `main` push.
- Preview remains 6 → 12 → 18 → 24 seconds, H.264 MP4 without audio and about 10 MiB safety budget.
- One process/one Railway replica; no PostgreSQL or broker decision before measurement.
- Existing report outbox, media CAS and Telegram rate handling must remain correct.
- Additive migration only; external staging snapshot/export and R1 online backup/restore precede schema deploy.
- Full test gate, reviewed diff, clean commit snapshot and pinned staging target for each deploy.

## Review Focus

- Destination added midstream must not see earlier samples from other chats; Task 3 test.
- Restart after queue claim must make expired lease retryable without losing a job; Task 4 test.
- Telegram RetryAfter must change job due time without sleeping Twitch poll; Task 5 test.
- Unsubscribe/new logical stream must not send an old queued post; Task 5 test.
- Crash after external Telegram success and before DB acknowledgement can duplicate; Task 5 test/documentation must show the at-least-once boundary.

---

### Task 1: Reproducible baseline harness

**Files:** Create `scripts/load_harness.py`, `tests/test_load_harness.py`, `docs/audits/2026-09-30-r3-baseline.json`; modify `docs/STATUS.md`.

**Interfaces:** `run_profile(destinations: int, *, seed: int, db_path: str, rounds: int) -> dict` returns config, operation counts, p50/p95/p99 in milliseconds, throughput, DB/WAL bytes and process RSS. CLI accepts `--destinations`, `--rounds`, `--seed`, `--output` and rejects an existing/non-temporary DB path.

- [x] Write failing deterministic test for 100 synthetic destinations, fixed seed, metric fields and refusal to overwrite an existing DB.
- [x] Run focused test, verify RED; implement isolated harness using synthetic Twitch observations, no Telegram sends and temporary SQLite.
- [x] Run focused test, verify GREEN; execute 1k/5k/10k baseline with the same seed, save JSON and explain measured limits without claiming 20–40k readiness.
- [x] Review output for personal data and commit code, test and baseline artifact.

### Task 2: Additive schema and migration evidence

**Files:** Modify `bot/database.py`; create `tests/test_growth_schema.py`; modify `docs/runbooks/staging-backup-rollback.md`.

**Interfaces:** `schema_migrations`, `stream_observations`, `stream_observation_destinations`, `notification_jobs` are created idempotently by `Database.connect()`; `Database.schema_versions() -> list[str]` exposes applied named versions for local/staging checks.

- [ ] Write failing tests for fresh DB, old DB migration, second startup idempotence, unique keys and rollback on injected migration failure.
- [ ] Run focused RED; add tables/indexes and explicit version ledger in the existing `BEGIN IMMEDIATE` migration boundary.
- [ ] Run focused GREEN and existing migration/retention tests; review `EXPLAIN QUERY PLAN` on claim and sample reads.
- [ ] Before staging schema deploy, complete external staging snapshot/export and online backup/restore drill; record paths and integrity without exposing tokens/user data.
- [ ] Commit schema package; do not deploy until Task 3 reader/writer path and Task 4 queue model are tested together.

### Task 3: Shared observations and legacy report compatibility

**Files:** Modify `bot/database.py`, `bot/poller.py`; create `tests/test_shared_stream_observations.py`; update R3 baseline comparison.

**Interfaces:** `Database.record_stream_observation(login, stream_id, sampled_at, viewer_count, title, game_name, chat_ids) -> None` writes one observation and membership rows in one transaction for new streams, while streams already present in legacy `stream_samples` keep legacy writes. Existing `get_stream_samples(chat_id, login, stream_id)` returns the same tuple sequence interface from the selected source.

- [ ] Write failing tests for N destinations sharing one row, later joiner window, legacy in-progress stream, reconnect logical ID, report order, retention safety and all existing latest-sample readers (`list_live_channels`, `get_live_post_details`, owner live snapshot).
- [ ] Run focused RED; change poller to collect per-logical-stream sample memberships and write one DB batch after destination processing.
- [ ] Run focused GREEN plus report/preview/regression tests; compare size and p95 on the synthetic harness.
- [ ] Review source selection and cutoff around active sessions; commit package.

### Task 4: Durable job model

**Files:** Create `bot/notification_queue.py`, `tests/test_notification_queue.py`; modify `bot/database.py`, `bot/admin_metrics.py`.

**Interfaces:** `NotificationQueue.enqueue(...)`, `claim_due(now, limit, lease_seconds)`, `ack(job_id)`, `defer(job_id, due_at, error_class)`, `fail(job_id, error_class)`, `depth_snapshot(now)` use `notification_jobs`. Unique `(kind, chat_id, twitch_login, logical_stream_id, payload_version)` is the idempotency key. Leases expire and are reclaimable.

- [ ] Write failing tests for duplicate enqueue, ordered claims, concurrent claimers, lease expiry/restart, retry, terminal failure and depth/age metrics.
- [ ] Run focused RED; implement short SQL transactions and bounded result sets.
- [ ] Run focused GREEN and owner snapshot tests; inspect query plans and absence of secrets/PII in metrics.
- [ ] Commit independently testable queue package.

### Task 5: Bounded Telegram worker and staged cutover

**Files:** Create `bot/notification_worker.py`, `tests/test_notification_worker.py`; modify `bot/poller.py`, `main.py`, `bot/config.py`, `bot/database.py`.

**Interfaces:** `NotificationWorker(queue, send_job, *, max_concurrency, per_chat_interval)` drains due jobs in a separate task. Staging-only `NOTIFICATION_QUEUE_ENABLED` switches a destination to enqueue mode; enqueue and tracked transition are one DB transaction. `send_job` reuses existing live-post composition and CAS update; report outbox stays separate.

- [ ] Write failing tests for slow fake Telegram not delaying `_check_streams()`, bound concurrency, per-chat spacing, RetryAfter scheduling, transient/terminal errors, stale jobs and no duplicate normal retry.
- [ ] Run focused RED; implement worker and new go-live cutover behind staging-only flag.
- [ ] Test crash/restart boundary explicitly and document the possible duplicate after send-before-ack.
- [ ] Extend queue cutover to live post updates and offline cleanup only after go-live tests pass; preserve media CAS and preview file-id reuse.
- [ ] Run focused/full regression suite, review diff and commit each go-live and update/cleanup package separately.

### Task 6: Preview isolation, staging validation and decision

**Files:** Add `tests/test_growth_preview_isolation.py`, update `scripts/load_harness.py`, `docs/audits/2026-09-30-r3-results.md`, `docs/STATUS.md`, `docs/DECISIONS.md`.

**Interfaces:** Harness adds preview 1/2/4 concurrency profiles with fake renderer/sender and queue depth/lag metrics; staging panel reports pending/leased/failed job counts and oldest due age.

- [ ] Write failing test that blocked preview capture/render/send does not block poll or normal notification worker; preserve existing 2 capture/1 artifact defaults.
- [ ] Run focused GREEN and full suite; review exact staging target, deploy only committed snapshot with R1 guard.
- [ ] Run `getMe`, `/healthz`, queue/recovery staging smoke, testbot E2E and synthetic load; record p50/p95/p99, CPU/RAM/disk, WAL growth and any delivery gap.
- [ ] Decide SQLite vs PostgreSQL from measured data; record decision and limitations, update STATUS/DECISIONS and commit documents.

## Self-review

Tasks 1–6 cover measurements, shared data, queue, bounded delivery, preview isolation, backup and staging acceptance. Interfaces are named once and consumed in later tasks. Review Focus cases each have an owning test step. Additive tables and a feature flag make staging rollback possible without touching production. No placeholder work or real payment/provider action is included.
