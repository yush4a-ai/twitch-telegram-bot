"""Bounded canonical reconciliation with durable SQLite leases and retry budget."""

import asyncio
import time
import uuid

from .billing_models import ReconcileSummary
from .billing_provider import PaymentVerificationError, ProviderRateLimited


BACKOFF = (5, 15, 30, 60, 120, 300, 300, 300)

# Ручная проверка не должна быть тупиком: подтверждённая оплата обязана дойти до
# человека, даже если первый раз сверка не сошлась (например, изменились правила
# суммы или провайдер отдал неполные данные). Перепроверяем редко и не бесконечно.
MANUAL_RECHECK_SECONDS = 3600
MANUAL_RECHECK_LIMIT = 24


class PaymentReconciler:
    def __init__(self, service):
        self.service = service
        self._lock = asyncio.Lock()
        self._task = None
        self._owner = uuid.uuid4().hex

    async def _acquire(self, *, now):
        async with self.service._store.transaction() as conn:
            provider = self.service._provider.provider_id
            await conn.execute("INSERT OR IGNORE INTO billing_worker_lease(provider,owner,lease_until) VALUES (?,NULL,NULL)", (provider,))
            result = await conn.execute(
                "UPDATE billing_worker_lease SET owner=?,lease_until=? WHERE provider=? AND (owner IS NULL OR lease_until<=?)",
                (self._owner, now+60, provider, now))
            return result.rowcount == 1

    async def _heartbeat(self, *, now):
        async with self.service._store.transaction() as conn:
            result = await conn.execute("UPDATE billing_worker_lease SET lease_until=? WHERE provider=? AND owner=?",
                (now+60, self.service._provider.provider_id, self._owner))
            return result.rowcount == 1

    async def _release(self):
        async with self.service._store.transaction() as conn:
            await conn.execute("UPDATE billing_worker_lease SET owner=NULL,lease_until=NULL WHERE provider=? AND owner=?",
                (self.service._provider.provider_id, self._owner))

    async def _claim(self, *, now):
        service = self.service
        async with service._store.transaction() as conn:
            inbox = await (await conn.execute(
                "SELECT event_key,transaction_id,raw_status,reconcile_count FROM billing_provider_inbox "
                "WHERE provider=? AND state IN ('pending','reconciling') AND COALESCE(next_reconcile_at,received_at)<=? "
                "AND (lease_until IS NULL OR lease_until<=?) ORDER BY received_at,event_key LIMIT 10",
                (service._provider.provider_id, now, now),
            )).fetchall()
            for key, reference, raw_status, count in inbox:
                attempt = await (await conn.execute(
                    "SELECT attempt_id,reconcile_count,lease_until FROM billing_payment_attempts WHERE provider=? AND provider_reference=?",
                    (service._provider.provider_id, reference),
                )).fetchone()
                if attempt is not None and attempt[2] is not None and attempt[2] > now:
                    continue
                count = max(count, attempt[1] if attempt else 0)
                if count >= 8:
                    await conn.execute("UPDATE billing_provider_inbox SET state='manual_review',lease_until=NULL,next_reconcile_at=NULL WHERE provider=? AND event_key=?", (service._provider.provider_id, key))
                    if attempt:
                        await conn.execute("UPDATE billing_payment_attempts SET state='manual_review',lease_until=NULL,next_reconcile_at=NULL WHERE attempt_id=?", (attempt[0],))
                    continue
                await conn.execute("UPDATE billing_provider_inbox SET state='reconciling',reconcile_count=?,lease_until=? WHERE provider=? AND event_key=?", (count+1, now+10, service._provider.provider_id, key))
                if attempt:
                    await conn.execute("UPDATE billing_payment_attempts SET state='reconciling',reconcile_count=?,lease_until=? WHERE attempt_id=?", (count+1, now+10, attempt[0]))
                return (key, reference, raw_status, count+1, attempt[0] if attempt else None)
            attempt = await (await conn.execute(
                "SELECT attempt_id,provider_reference,reconcile_count FROM billing_payment_attempts "
                "WHERE provider=? AND provider_reference IS NOT NULL AND state IN ('pending','reconciling','creation_unknown') "
                "AND order_id IN (SELECT order_id FROM billing_orders WHERE status='pending') "
                "AND COALESCE(next_reconcile_at,created_at)<=? "
                "AND (lease_until IS NULL OR lease_until<=?) "
                "ORDER BY COALESCE(next_reconcile_at,created_at),created_at LIMIT 1",
                (service._provider.provider_id, now, now),
            )).fetchone()
            recheck = False
            if attempt is None:
                # Отложенная перепроверка того, что ушло в ручную проверку.
                attempt = await (await conn.execute(
                    "SELECT attempt_id,provider_reference,reconcile_count FROM billing_payment_attempts "
                    "WHERE provider=? AND provider_reference IS NOT NULL AND state='manual_review' "
                    "AND order_id IN (SELECT order_id FROM billing_orders WHERE status='pending') "
                    "AND next_reconcile_at IS NOT NULL AND next_reconcile_at<=? AND reconcile_count<? "
                    "AND (lease_until IS NULL OR lease_until<=?) "
                    "ORDER BY next_reconcile_at LIMIT 1",
                    (service._provider.provider_id, now, MANUAL_RECHECK_LIMIT, now),
                )).fetchone()
                recheck = attempt is not None
            if attempt is None:
                return None
            attempt_id, reference, count = attempt
            if not recheck and count >= 8:
                await conn.execute("UPDATE billing_payment_attempts SET state='manual_review',lease_until=NULL,next_reconcile_at=? WHERE attempt_id=?",
                    (now + MANUAL_RECHECK_SECONDS, attempt_id))
                return None
            await conn.execute("UPDATE billing_payment_attempts SET state='reconciling',reconcile_count=?,lease_until=? WHERE attempt_id=?", (count+1, now+10, attempt_id))
            return (None, reference, None, count+1, attempt_id)

    async def _finish(self, claim, *, state, now, delay=None, evidence=None):
        key, reference, _, count, attempt_id = claim
        service = self.service
        if attempt_id is None and evidence is not None:
            candidate = await service._store.get_attempt(evidence.attempt_id)
            if candidate is not None and candidate.provider_reference == reference:
                attempt_id = candidate.attempt_id
        if state == "pending" and count >= 8:
            state = "manual_review"
        if state == "pending":
            next_at = now + (delay if delay is not None else BACKOFF[min(count-1, 7)])
        elif state == "manual_review" and key is None and count < MANUAL_RECHECK_LIMIT:
            # Попытка вернётся на перепроверку: иначе оплата зависнет навсегда.
            next_at = now + MANUAL_RECHECK_SECONDS
        else:
            next_at = None
        async with service._store.transaction() as conn:
            if key is not None:
                await conn.execute("UPDATE billing_provider_inbox SET state=?,next_reconcile_at=?,lease_until=NULL WHERE provider=? AND event_key=?", (state, next_at, service._provider.provider_id, key))
            if attempt_id is not None:
                await conn.execute("UPDATE billing_payment_attempts SET state=?,next_reconcile_at=?,lease_until=NULL,reconcile_count=MAX(reconcile_count,?) WHERE attempt_id=?", (state, next_at, count, attempt_id))
        return state

    async def reconcile_due(self, *, now, limit=10):
        self.service._check_now(now)
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("invalid reconciliation batch")
        if (not self.service._local_runtime() or getattr(self.service._provider, "can_reconcile", True) is False
                or not callable(getattr(self.service._provider, "get_payment_status", None))):
            return ReconcileSummary()
        if self._lock.locked():
            return ReconcileSummary(deferred=1)
        attempted = deferred = applied = manual = 0
        async with self._lock:
            if not await self._acquire(now=now):
                return ReconcileSummary(deferred=1)
            started = time.monotonic()
            try:
                for _ in range(limit):
                    if not await self._heartbeat(now=now+time.monotonic()-started):
                        break
                    claim = await self._claim(now=now)
                    if claim is None:
                        break
                    attempted += 1
                    try:
                        evidence = await asyncio.wait_for(self.service._provider.get_payment_status(claim[1]), timeout=5)
                        if evidence.transaction_id != claim[1]:
                            raise PaymentVerificationError("canonical transaction mismatch")
                        if claim[2] == "CHARGEBACKED" and evidence.status != "refunded":
                            raise PaymentVerificationError("chargeback requires canonical resolution")
                        result = await self.service.apply_payment_evidence(evidence, now=now)
                        state = "pending" if result.state == "pending" else "manual_review" if result.state == "manual_review" else "done"
                        final = await self._finish(claim, state=state, now=now, evidence=evidence)
                        applied += result.state == "applied"
                        deferred += final == "pending"
                        manual += final == "manual_review"
                    except ProviderRateLimited as error:
                        state = await self._finish(claim, state="pending", now=now, delay=error.retry_after)
                        deferred += state == "pending"
                        manual += state == "manual_review"
                    except (PaymentVerificationError, ValueError, TypeError, AttributeError):
                        await self._finish(claim, state="manual_review", now=now)
                        manual += 1
                    except asyncio.CancelledError:
                        await asyncio.shield(self._finish(claim, state="pending", now=now))
                        raise
                    except Exception:
                        state = await self._finish(claim, state="pending", now=now)
                        deferred += state == "pending"
                        manual += state == "manual_review"
            finally:
                await asyncio.shield(self._release())
        return ReconcileSummary(attempted, deferred, applied, manual)

    def start(self, *, interval=5):
        if type(interval) not in (int, float) or interval < 5:
            raise ValueError("invalid reconciliation interval")
        if self._task is not None:
            raise RuntimeError("reconciliation already running")
        async def run():
            while True:
                await self.reconcile_due(now=time.time(), limit=10)
                await asyncio.sleep(interval)
        self._task = asyncio.create_task(run(), name="payment-reconciliation")

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
