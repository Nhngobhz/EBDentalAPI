"""
ABA PayWay integration - the alternative KHQR provider (see config.py's qr_provider).

PayWay is ABA Bank's merchant payment gateway. Unlike the Bakong-direct path
(services/khqr.py), PayWay both GENERATES the KHQR (their Purchase API returns a
ready `qr_string` - still a standard KHQR/EMV payload, rendered by the exact same
frontend QR modal) and CONFIRMS payment (their Check Transaction API), so no Bakong
developer token is needed. What it needs instead is a PayWay merchant account from
ABA: `merchant_id` + API key, and the PayWay integration team whitelisting this
server's IP/domain - until they do, every call here is rejected upstream.

API reference (developer.payway.com.kh):
  POST /api/payment-gateway/v1/payments/purchase        (multipart/form-data)
  POST /api/payment-gateway/v1/payments/check-transaction-2  (application/json)

Both are authenticated by an HMAC-SHA512-base64 `hash` over a fixed concatenation
of the request's field values (empty string for anything not sent), keyed with the
merchant API key. Getting the concatenation order wrong yields PayWay status code 5
("invalid hash") - the orders below match the official docs, don't reorder them.

`tran_id` is the PendingCheckout.reference the QR was issued under (a yymmddhhmmss
code, well under PayWay's 20-char cap) - NOT an order number, because a customer's
pay-by-QR purchase has no order until the payment is confirmed. It is copied onto the
order as `payment_reference` when the order is finally written, which is what ties a
PayWay transaction to the sale it produced.
"""
import base64
import hashlib
import hmac
from datetime import datetime, timezone
from decimal import Decimal

from app.config import settings
from app.core.logging_conf import get_logger

logger = get_logger("payway")


class PayWayError(Exception):
    """Raised when PayWay can't create a QR (network failure, rejected hash,
    unwhitelisted IP, ...). create_order turns this into a friendly 'choose Cash
    instead' 400 - a gateway hiccup must never strand the customer."""


def _hash(*values: str) -> str:
    """HMAC-SHA512 over the concatenated values, base64-encoded - PayWay's request
    signature. Keyed with the merchant API key."""
    payload = "".join(values).encode("utf-8")
    digest = hmac.new(settings.PAYWAY_API_KEY.encode("utf-8"), payload, hashlib.sha512).digest()
    return base64.b64encode(digest).decode("ascii")


def _req_time() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def create_payway_khqr(amount: Decimal, tran_id: str) -> str:
    """Creates a PayWay purchase transaction with payment_option=abapay_khqr and
    returns the KHQR payload string to render. Synchronous on purpose - called from
    the (sync) create_order router, which FastAPI already runs in a threadpool."""
    import httpx  # local import, same pattern as services/telegram.py

    req_time = _req_time()
    amount_str = f"{amount:.2f}"  # USD, 2 decimals - must match the hashed value
    fields = {
        "req_time": req_time,
        "merchant_id": settings.PAYWAY_MERCHANT_ID,
        "tran_id": tran_id,
        "amount": amount_str,
        "type": "purchase",
        "payment_option": "abapay_khqr",
        "currency": "USD",
    }
    # Hash concatenation per the Purchase API docs. Fields we don't send (items,
    # shipping, names, urls, ...) contribute an empty string but MUST keep their
    # position in the sequence.
    fields["hash"] = _hash(
        req_time,
        settings.PAYWAY_MERCHANT_ID,
        tran_id,
        amount_str,
        "",  # items
        "",  # shipping
        "",  # firstname
        "",  # lastname
        "",  # email
        "",  # phone
        "purchase",  # type
        "abapay_khqr",  # payment_option
        "",  # return_url
        "",  # cancel_url
        "",  # continue_success_url
        "",  # return_deeplink
        "USD",  # currency
        "",  # custom_fields
        "",  # return_params
        "",  # payout
        "",  # lifetime
        "",  # additional_params
        "",  # google_pay_token
        "",  # skip_success_page
    )

    url = f"{settings.payway_base_url}/api/payment-gateway/v1/payments/purchase"
    try:
        with httpx.Client(timeout=20) as client:
            # multipart/form-data with plain fields, per the docs - the (None, value)
            # shape is how httpx sends a non-file multipart part.
            resp = client.post(url, files={k: (None, v) for k, v in fields.items()})
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:
        logger.warning("PayWay purchase call failed: %s: %s", type(exc).__name__, exc)
        raise PayWayError("Could not reach PayWay") from exc

    status = (body.get("status") or {})
    if str(status.get("code")) != "00":
        logger.warning(
            "PayWay rejected the purchase for tran_id %s: code=%s message=%s",
            tran_id, status.get("code"), status.get("message"),
        )
        raise PayWayError(f"PayWay rejected the transaction: {status.get('message')}")

    # The live API returns `qrString` (camelCase) even though PayWay's own docs
    # document it as `qr_string` - verified against the sandbox on 2026-07-29.
    # Both are accepted so a docs-conformant response would work too. The response
    # also carries `qrImage` (a base64 PNG) and `abapay_deeplink`, neither of which
    # is used: the frontend draws the QR from the string itself.
    qr_string = body.get("qrString") or body.get("qr_string")
    if not qr_string:
        logger.warning(
            "PayWay accepted the purchase for tran_id %s but returned no QR string (keys: %s)",
            tran_id, sorted(body),
        )
        raise PayWayError("PayWay returned no QR code")
    return qr_string


async def check_payway_payment(tran_id: str) -> bool:
    """Asks PayWay whether the purchase for `tran_id` (= our order_number) has been
    APPROVED. Same conservative contract as check_bakong_payment: True only on a
    positive confirmation, False on anything ambiguous so the poll just retries."""
    if not settings.payway_configured:
        return False

    import httpx

    req_time = _req_time()
    payload = {
        "req_time": req_time,
        "merchant_id": settings.PAYWAY_MERCHANT_ID,
        "tran_id": tran_id,
        "hash": _hash(req_time, settings.PAYWAY_MERCHANT_ID, tran_id),
    }
    url = f"{settings.payway_base_url}/api/payment-gateway/v1/payments/check-transaction-2"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 400:
                logger.warning(
                    "PayWay check-transaction returned HTTP %s: %s",
                    resp.status_code, resp.text[:300],
                )
                return False
            body = resp.json()
    except Exception as exc:
        logger.warning("PayWay payment check failed: %s: %s", type(exc).__name__, exc)
        return False

    if str((body.get("status") or {}).get("code")) != "00":
        # 6 = transaction not found (customer hasn't paid AND the QR was never
        # scanned into a pending state) - routine while waiting, not log-worthy.
        return False
    return (body.get("data") or {}).get("payment_status") == "APPROVED"
