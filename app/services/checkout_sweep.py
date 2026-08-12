"""
Background reconciliation for pay-by-QR checkouts.

A customer KHQR purchase writes no order until the payment is confirmed (see
routers/orders.py::create_checkout). The browser's poll is what normally notices the
payment - but the browser is the one thing here nobody controls. A customer who scans,
pays, and closes the tab would otherwise leave money received and no order at all, which
is a far worse failure than the unpaid-order rows this design exists to avoid.

So the server checks too. Every SWEEP_INTERVAL_SECONDS this re-asks the provider about
every checkout that has been issued a QR and not yet become an order, and materializes
any that have been paid - the same code path, the same row lock, and the same paid-order
Telegram alert the browser poll would have produced. Whoever gets there first wins;
`PendingCheckout.order_id` is what makes that safe.

It also prunes: a checkout whose QR expired long ago with no payment is litter, and
keeping it means asking the provider about it forever.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from app.core.logging_conf import get_logger
from app.database import SessionLocal
from app.models import PendingCheckout

logger = get_logger("checkout_sweep")

# Slow on purpose. The browser poll (every 3s) is what makes payment feel instant to a
# customer standing at the QR; this is the safety net behind it, and every tick costs one
# outbound API call per outstanding checkout.
SWEEP_INTERVAL_SECONDS = 60

# How long past its expiry a checkout keeps being checked. A payment can land in the last
# seconds before expiry and still be settling when it passes, so the window has to extend
# past it - this is not the same thing as the QR still being scannable.
GRACE_MINUTES = 30

# How long an expired, never-paid checkout is kept before deletion - measured from its
# expiry, not its creation. Long enough that staff can still find the reference if a
# customer turns up claiming they paid, short enough that the admin page's "Awaiting
# payment" card stays a to-do list rather than a graveyard of abandoned QR attempts.
#
# Only ever deletes checkouts that never became an order. A checkout that WAS paid has
# an order_id and is excluded from the prune entirely, so no record of a sale is at risk
# here however low this goes.
RETAIN_HOURS = 24


async def _reconcile_once() -> None:
    """One pass: materialize anything that got paid, then prune what's long dead."""
    # Imported here, not at module scope: routers.orders imports services.telegram which
    # imports app.config... and this module is imported by main.py at startup, so a
    # top-level import would close a cycle.
    from app.routers.orders import _checkout_is_paid, _materialize_checkout
    from app.schemas import OrderOut
    from app.services.telegram import deliver_order_alert

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        outstanding = (
            db.query(PendingCheckout)
            .filter(
                PendingCheckout.order_id.is_(None),
                PendingCheckout.expires_at > now - timedelta(minutes=GRACE_MINUTES),
            )
            .all()
        )
        for pending in outstanding:
            try:
                if not await _checkout_is_paid(pending):
                    continue
                # Re-read under a row lock before writing: the customer's own browser may
                # be polling this very checkout right now, and one payment must produce
                # exactly one order.
                locked = (
                    db.query(PendingCheckout)
                    .filter(PendingCheckout.id == pending.id)
                    .with_for_update()
                    .first()
                )
                if locked is None or locked.order_id is not None:
                    db.commit()
                    continue
                order = _materialize_checkout(db, locked)
                logger.info(
                    "Reconciled checkout %s -> order %s ($%.2f) - the browser never saw it",
                    locked.reference,
                    order.order_number,
                    order.grand_total,
                )
                await deliver_order_alert(OrderOut.model_validate(order))
            except Exception as exc:
                # One bad checkout must not stop the rest of the sweep, and must never
                # kill the loop - the next tick tries again.
                db.rollback()
                logger.warning(
                    "Checkout %s could not be reconciled: %s: %s",
                    pending.reference,
                    type(exc).__name__,
                    exc,
                )

        deleted = (
            db.query(PendingCheckout)
            .filter(
                PendingCheckout.order_id.is_(None),
                PendingCheckout.expires_at < now - timedelta(hours=RETAIN_HOURS),
            )
            .delete(synchronize_session=False)
        )
        if deleted:
            logger.info("Pruned %s expired unpaid checkout(s)", deleted)
        db.commit()
    finally:
        db.close()


async def run_checkout_sweep() -> None:
    """The loop itself, started as a background task by main.py's lifespan. Never raises:
    a sweep that dies silently would put us right back to relying on the browser."""
    while True:
        try:
            await _reconcile_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Checkout sweep pass failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
