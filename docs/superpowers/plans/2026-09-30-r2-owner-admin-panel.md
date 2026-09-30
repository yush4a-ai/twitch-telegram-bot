# R2 Owner Admin Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a staging-only, read-only browser panel for the TwitchSignalBot owner.

**Architecture:** Existing aiohttp listener mounts isolated `/admin` routes. A small session gate protects a read-only snapshot service that projects existing runtime and SQLite aggregates. Static HTML/CSS/JS renders the snapshot without external dependencies.

**Tech Stack:** Project `.venv` Python 3, aiohttp 3.14.3, aiosqlite 0.22.1, pytest, vanilla HTML/CSS/JS, Playwright for browser QA.

**Spec:** `docs/superpowers/specs/2026-09-30-r2-owner-admin-panel-design.md`

## Global Constraints

- Staging/local only with `ADMIN_PANEL_ACCESS_KEY` of at least 32 characters; production routes return 404.
- No secrets, chat IDs, OAuth states, DB paths or raw errors in responses/logs.
- Read-only snapshot; `/healthz` remains in-memory and preview-independent.
- Unknown delivery/preview observations remain unknown; no invented success.
- No CDN or new runtime dependency, no real payments or production changes.
- Full test gate, diff review, exact staging target and deployment identity before smoke.

## Review Focus

- Wrong/missing key or disabled environment: no operational HTML/JSON; Task 1 tests.
- Replay of expired or logged-out cookie: 401; Task 1 tests.
- DB failure during snapshot: in-memory health remains visible and DB values unknown; Task 2 tests.
- Duplicate `tracked_channels` for one Twitch login: one live row with destination count; Task 2 tests.
- Long Russian text/empty/error/stale state at 360 px: no overflow and clear recovery; Task 3 browser checks.

---

### Task 1: Access gate and HTTP routes

**Files:** Create `bot/admin_auth.py`, `bot/admin_web.py`, `tests/test_admin_web.py`; modify `bot/oauth.py`, `bot/config.py`, `main.py`.

**Interfaces:** `AdminAccess(key: str, *, enabled: bool)` owns sessions; `install_admin_routes(app: web.Application, access: AdminAccess, snapshot_provider: Callable[[], Awaitable[dict]]) -> None` mounts routes. `OAuthCallbackServer.set_admin_panel(...)` registers provider before `start()`.

- [x] Write failing tests for disabled routes, invalid/valid login, secure cookie, expiry, logout, rate limit, security headers and unauthorized API.
- [x] Run `.venv\Scripts\python.exe -m pytest tests/test_admin_web.py -q -p no:cacheprovider` and observe expected failures.
- [x] Implement access/session and routes; use 404 when disabled and 401 on protected API. Configure key in `load_config()` with staging guard.
- [x] Run focused tests and existing OAuth health tests; review exposure of secrets and routes.
- [x] Commit tested package.

### Task 2: Read-only operational snapshot

**Files:** Create `bot/admin_metrics.py`, `tests/test_admin_metrics.py`; modify `bot/database.py`, `main.py`.

**Interfaces:** `Database.get_admin_live_streams(limit: int = 20) -> list[dict]`; `AdminSnapshot(db, poller, follow_listener, token_store, preview_manager, *, db_path, telegram_polling_provider).collect() -> dict`. The provider is called only by authenticated API.

- [ ] Write failing tests for live deduplication, empty data, separate Telegram/Twitch/preview states, absent preview, DB timeout/error, safe error class names, resource unknowns.
- [ ] Run focused tests to confirm failure.
- [ ] Implement parameterized read-only query and bounded snapshot collection; call no external API and never log/output secrets or paths.
- [ ] Run focused tests plus existing DB and preview health tests; review query cost and response schema.
- [ ] Commit tested package.

### Task 3: Responsive panel and browser journey

**Files:** Create `bot/admin_ui/index.html`, `bot/admin_ui/panel.css`, `bot/admin_ui/panel.js`, `tests/test_admin_ui.py`; modify `bot/admin_web.py`.

**Interfaces:** `GET /admin`, `/admin/panel.css`, `/admin/panel.js`, `/admin/api/snapshot`; JS renders exact Task 2 schema. Login and logout use Task 1 routes.

- [ ] Write failing route/static assertions and browser scenario for sign-in, refresh, logout, denied access, empty/error/stale presentation.
- [ ] Run focused tests to confirm failure.
- [ ] Implement UI to `DESIGN.md` and craft floor; use textContent for all dynamic values, responsive live rows, focus states, dark/light and reduced motion.
- [ ] Run route tests, Playwright at 360/390/768/1440 and screenshot/keyboard/overflow checks in one batch. Correct observed defects and recheck.
- [ ] Commit tested package.

### Task 4: Staging evidence and documentation

**Files:** Modify `docs/STATUS.md`, `docs/DECISIONS.md`, `docs/runbooks/staging-deploy.md`, this plan.

**Interfaces:** Existing `scripts/staging_deploy.py` guard and Railway staging IDs remain authoritative.

- [ ] Self-review spec coverage, `git diff`, security and UI review; run full pytest suite on exact clean commit.
- [ ] Set isolated staging-only key without printing it; verify staging target and deploy with R1 guard.
- [ ] Confirm new deployment ID/SHA, `getMe=TwitchSignalTestbot`, `/healthz`, denied API, authenticated API and browser smoke. Capture screenshots; mark unavailable Telegram delivery and native-device checks unverified.
- [ ] Update STATUS/DECISIONS with evidence and limitations; commit documents.

## Self-review

Spec sections map to Tasks 1–4. Task 1 handles access and environment gating; Task 2 handles every dashboard datum and unknown semantics; Task 3 handles browser states and accessibility; Task 4 handles staging identity and evidence. Type names and routes are consistent. `DESIGN.md` and `PRODUCT.md` are durable inputs to Task 3. No TODO/TBD or production action remains in this plan.
