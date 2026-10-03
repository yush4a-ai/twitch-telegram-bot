"""Read-only subscription view shared by Telegram and signed Mini App routes."""
import time
from .entitlements import resolve_effective_viewer
from .viewer_trial import ViewerTrialService


class SubscriptionService:
    def __init__(self, db, *, test_user_ids=frozenset()):
        self.db=db
        self.trial=ViewerTrialService(db)
        self.test_user_ids=test_user_ids

    def allowed(self,user_id):
        return user_id in self.test_user_ids

    async def owned_streamer_grant(self, user_id: int, now: float):
        # Subscription ownership survives unlink. Legacy unbound grants remain
        # visible only through their existing verified broadcaster binding.
        row = await (await self.db.conn.execute(
            "SELECT g.grant_id,g.source,g.expires_at FROM entitlement_grants g "
            "WHERE g.subject_kind='streamer' AND g.plan='streamer_plus' "
            "AND g.revoked_at IS NULL AND g.starts_at<=? AND g.expires_at>? "
            "AND (g.beneficiary_telegram_user_id=? OR (g.beneficiary_telegram_user_id IS NULL "
            "AND EXISTS (SELECT 1 FROM streamer_identities i WHERE i.telegram_user_id=? "
            "AND i.broadcaster_id=g.subject_id))) ORDER BY g.expires_at DESC LIMIT 1",
            (now, now, user_id, user_id),
        )).fetchone()
        return row

    async def owned_streamer(self, user_id: int, now: float):
        row=await self.owned_streamer_grant(user_id,now)
        return row[1:] if row else None

    async def order_summary(self, order, user_id: int, now: float):
        monetary = order.provider in {"platega", "telegram_stars"}
        status = order.status
        if status == "pending" and now >= order.checkout_expires_at:
            status = "expired"
        elif status == "paid" and order.paid_at is not None and now >= order.paid_at + order.duration_seconds:
            status = "expired"
        viewer = await resolve_effective_viewer(self.db, user_id, now=now)
        return {
            "order_id": order.order_id, "product": order.plan, "method": order.method,
            "financial_status": order.financial_status if monetary else None,
            "status": status, "created_at": order.created_at,
            "checkout_expires_at": order.checkout_expires_at,
            "access_starts_at": order.access_starts_at,
            "access_expires_at": order.access_expires_at,
            "effective_access": {"viewer": viewer.active,
                                 "streamer": await self.owned_streamer(user_id, now) is not None},
            "monetary": monetary,
        }

    async def state(self,user_id,*,now=None):
        now = time.time() if now is None else now
        identity = await self.db.get_streamer_identity(user_id)
        viewer = await self.db.get_current_plus_grant(user_id, "viewer_plus", now=now)
        effective_viewer = await resolve_effective_viewer(self.db, user_id, now=now)
        trial_status = await self.trial.status(user_id, now=now)
        streamer = await self.owned_streamer(user_id, now)
        publishing = await self.db.get_current_plus_grant(user_id, "streamer_plus", now=now)
        orders = await self.db.list_billing_orders_for_user(user_id)

        return {
            "viewer": {"active": effective_viewer.active,
                       "expires_at": effective_viewer.expires_at,
                       "source": viewer[0] if viewer else ("streamer_plus" if effective_viewer.active else None),
                       "sources": [{"grant_id": s.grant_id, "product_id": s.product_id,
                                    "starts_at": s.starts_at, "expires_at": s.expires_at}
                                   for s in effective_viewer.sources],
                       "test_trial_available": self.allowed(user_id) and not trial_status.used and not effective_viewer.active,
                       "test_trial_used": self.allowed(user_id) and trial_status.used,
                       "test_trial_active": self.allowed(user_id) and trial_status.active,
                       "test_trial_expires_at": trial_status.expires_at if self.allowed(user_id) else None},
            "streamer": {"linked": identity is not None,
                         "publishing_access": publishing is not None,
                         "twitch_login": identity[1] if identity else None,
                         "active": streamer is not None,
                         "expires_at": streamer[1] if streamer else None,
                         "source": streamer[0] if streamer else None},
            "history": [await self.order_summary(order, user_id, now) for order in orders],
            "test_checkout_available": self.allowed(user_id),
            "money_charged": False,
        }
