"""
KHQR (Bakong) payment QR generation and payment-status checking.

KHQR is Cambodia's national EMVCo-based QR payment standard. A "dynamic" KHQR
carries the exact amount, so the customer just scans and confirms in their
banking app - no typing. The payload is a flat TLV string (2-digit tag,
2-digit length, value) ending in a CRC-16 checksum, per the NBC KHQR spec:

    00 payload format "01"
    01 point-of-initiation "12" (dynamic - amount included)
    29 merchant account info (individual): sub-tag 00 = Bakong account id
    52 merchant category code
    53 currency (840 = USD - every price in this system is in dollars)
    54 amount
    58 country "KH"
    59 merchant name
    60 merchant city
    62 additional data: sub-tag 01 = bill number (our order_number)
    99 sub-tag 00 = creation timestamp in ms (KHQR-specific)
    63 CRC-16/CCITT-FALSE over everything incl. the "6304" prefix

The MD5 of the final payload is how Bakong's open API identifies the
transaction (check_transaction_by_md5) - both the payload and its MD5 are
persisted on the Order so payment can be confirmed later (see
routers/orders.py::check_payment_status).

All merchant details come from settings (BAKONG_ACCOUNT_ID etc.) - if they're
not configured, KHQR checkout is refused at order creation, never half-built.
"""
import hashlib
import time
from decimal import Decimal

from app.config import settings
from app.core.logging_conf import get_logger

logger = get_logger("khqr")

# ISO 4217 numeric code embedded in tag 53. Every price in this system is USD.
_CURRENCY_USD = "840"
# Generic "miscellaneous retail" MCC - Bakong accepts it for individual accounts.
_MERCHANT_CATEGORY_CODE = "5999"


def _tlv(tag: str, value: str) -> str:
    """One EMV TLV field: 2-digit tag + 2-digit zero-padded length + value."""
    return f"{tag}{len(value):02d}{value}"


def _crc16_ccitt(data: str) -> str:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF), uppercase hex - the checksum
    algorithm the EMV QR spec mandates for tag 63."""
    crc = 0xFFFF
    for byte in data.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def _parse_tlv(payload: str) -> list[tuple[str, str]]:
    """Splits an EMV payload into (tag, value) pairs - the inverse of _tlv()."""
    fields = []
    i = 0
    while i + 4 <= len(payload):
        tag = payload[i:i + 2]
        try:
            length = int(payload[i + 2:i + 4])
        except ValueError:
            raise ValueError(f"Malformed KHQR template: bad length at offset {i}")
        fields.append((tag, payload[i + 4:i + 4 + length]))
        i += 4 + length
    return fields


def _build_from_template(amount: Decimal, bill_number: str) -> str:
    """Builds a dynamic KHQR by reusing the payee routing data from a real static
    KHQR (KHQR_STATIC_TEMPLATE - the "my QR / receive money" code from the bank's
    app), injecting the amount and bill number.

    Why this exists: a bank's static QR is often NOT a plain
    `name@bank` Bakong alias. ABA's dual-currency personal QR, for instance, puts
    the *institution* id in tag 29 sub-field 00 (`abaakhppxxx@abaa`) and the actual
    account number in sub-field 01, plus an ABA-proprietary tag 40 carrying a P2P
    token and both the KHR and USD account numbers. Rebuilding such a QR from just
    sub-field 00 would produce a code that names the bank but no account. So
    everything in the merchant-account tag range (26-51) is copied through
    byte-for-byte and only the transaction-specific fields are replaced.

    Point-of-initiation is flipped 11 (static) -> 12 (dynamic), since the result
    now carries a fixed amount."""
    fields = dict(_parse_tlv(settings.KHQR_STATIC_TEMPLATE.strip()))

    # Tags 26-51 are the EMV "merchant account information" range - the payee
    # routing data, whatever proprietary shape a given bank uses. Copied verbatim,
    # in ascending tag order as the spec requires.
    account_tags = "".join(
        _tlv(tag, fields[tag])
        for tag in sorted(fields)
        if tag.isdigit() and 26 <= int(tag) <= 51
    )
    if not account_tags:
        raise RuntimeError(
            "KHQR_STATIC_TEMPLATE has no merchant-account tag (26-51) - is it a real KHQR?"
        )

    # Payee-identifying fields keep the template's own values when it has them: the
    # name in particular should match what the bank has on file for the account,
    # not a marketing name the bank never heard of.
    payload = (
        _tlv("00", "01")
        + _tlv("01", "12")
        + account_tags
        + _tlv("52", fields.get("52") or _MERCHANT_CATEGORY_CODE)
        + _tlv("53", _CURRENCY_USD)
        + _tlv("54", f"{amount:.2f}")
        + _tlv("58", fields.get("58") or "KH")
        + _tlv("59", (fields.get("59") or settings.KHQR_MERCHANT_NAME)[:25])
        + _tlv("60", (fields.get("60") or settings.KHQR_MERCHANT_CITY)[:15])
        + _tlv("62", _tlv("01", bill_number[:25]))
        + _tlv("99", _tlv("00", str(int(time.time() * 1000))))
    )
    return payload + "6304" + _crc16_ccitt(payload + "6304")


def _build_from_account_id(amount: Decimal, bill_number: str) -> str:
    """The simple case: a true Bakong account alias (`name@bank`) is all the payee
    routing a KHQR needs, so the whole payload is built from scratch."""
    payload = (
        _tlv("00", "01")
        + _tlv("01", "12")
        + _tlv("29", _tlv("00", settings.BAKONG_ACCOUNT_ID))
        + _tlv("52", _MERCHANT_CATEGORY_CODE)
        + _tlv("53", _CURRENCY_USD)
        + _tlv("54", f"{amount:.2f}")
        + _tlv("58", "KH")
        + _tlv("59", settings.KHQR_MERCHANT_NAME[:25])
        + _tlv("60", settings.KHQR_MERCHANT_CITY[:15])
        + _tlv("62", _tlv("01", bill_number[:25]))
        + _tlv("99", _tlv("00", str(int(time.time() * 1000))))
    )
    return payload + "6304" + _crc16_ccitt(payload + "6304")


def build_khqr(amount: Decimal, bill_number: str) -> tuple[str, str]:
    """Builds the dynamic KHQR payload for `amount` USD and returns (payload, md5).

    Uses KHQR_STATIC_TEMPLATE when set (see _build_from_template - the right choice
    for a bank's own personal/P2P QR), otherwise BAKONG_ACCOUNT_ID. Raises
    RuntimeError if neither is configured - callers should have checked
    settings.khqr_configured first. `scripts/decode_khqr.py` renders either result
    back out field-by-field, which is the quickest way to eyeball one."""
    if settings.KHQR_STATIC_TEMPLATE:
        payload = _build_from_template(amount, bill_number)
    elif settings.BAKONG_ACCOUNT_ID:
        payload = _build_from_account_id(amount, bill_number)
    else:
        raise RuntimeError(
            "KHQR is not configured (set KHQR_STATIC_TEMPLATE or BAKONG_ACCOUNT_ID)"
        )
    return payload, hashlib.md5(payload.encode("utf-8")).hexdigest()


async def check_bakong_payment(md5: str) -> bool:
    """Asks Bakong's open API whether the transaction for this KHQR payload's MD5
    has gone through. Returns True only on a positive confirmation; any error
    (no token configured, network failure, token expired) returns False so the
    caller just keeps the order unpaid and tries again on the next poll -
    payment must never be confirmed on ambiguity."""
    if not settings.BAKONG_API_TOKEN:
        return False

    import httpx  # local import, same pattern as services/telegram.py

    url = f"{settings.BAKONG_API_BASE.rstrip('/')}/v1/check_transaction_by_md5"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={"md5": md5},
                headers={"Authorization": f"Bearer {settings.BAKONG_API_TOKEN}"},
            )
            if resp.status_code >= 400:
                # 401 here almost always means the Bakong developer token expired
                # (they're renewed at api-bakong.nbc.gov.kh) - worth a distinct log.
                logger.warning(
                    "Bakong check_transaction_by_md5 returned HTTP %s: %s",
                    resp.status_code, resp.text[:300],
                )
                return False
            body = resp.json()
            # responseCode 0 = transaction found (i.e. paid); 1 = not found yet.
            return body.get("responseCode") == 0
    except Exception as exc:
        logger.warning("Bakong payment check failed: %s: %s", type(exc).__name__, exc)
        return False
