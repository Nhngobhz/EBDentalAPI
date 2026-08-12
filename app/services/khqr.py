"""
KHQR (Bakong) payment QR generation and payment-status checking.

KHQR is Cambodia's national EMVCo-based QR payment standard. A "dynamic" KHQR
carries the exact amount, so the customer just scans and confirms in their
banking app - no typing. The payload is a flat TLV string (2-digit tag,
2-digit length, value) ending in a CRC-16 checksum, per the NBC KHQR spec:

    00 payload format "01"
    01 point-of-initiation "12" (dynamic - amount included)
    29 merchant account info (individual): sub-tags 00 Bakong account id,
       01 account information, 02 acquiring bank
    52 merchant category code
    53 currency (840 = USD - every price in this system is in dollars)
    54 amount
    58 country "KH"
    59 merchant name
    60 merchant city
    62 additional data: sub-tag 01 = bill number (our order_number)
    99 KHQR-specific timestamps in ms: sub-tag 00 creation, 01 expiry
    63 CRC-16/CCITT-FALSE over everything incl. the "6304" prefix

Bakong has no server-side "generate a QR" endpoint - NBC's own SDKs assemble the
payload locally exactly as above, and the open API is only for verification. So
BAKONG_API_TOKEN is needed by check_bakong_payment() below, never by the builders.

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
    """Fallback builder, used only when BAKONG_ACCOUNT_ID is unset: builds a dynamic
    KHQR by reusing the payee routing data from a real static KHQR
    (KHQR_STATIC_TEMPLATE - the "my QR / receive money" code from the bank's app),
    injecting the amount and bill number.

    Because it copies the bank's tags verbatim it also carries whatever proprietary
    ones the bank added, which may route the payment over the bank's own rail rather
    than Bakong's - in which case check_bakong_payment() will never see it and staff
    must confirm manually. Prefer _build_from_account_id.

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
    """Builds a KHQR from scratch in the spec's "individual" shape - what NBC's own
    SDKs emit for BakongKHQR.generateIndividual(), and the only form guaranteed to
    route over the Bakong rail (which is what makes check_bakong_payment() below
    able to see the transaction at all).

    Tag 29 carries all three payee sub-fields the spec defines:
        00  Bakong account id      mandatory, <=32 - `name@bank`, or for a bank
                                   account the *bank's* id (ABA: abaakhppxxx@abaa)
        01  account information    optional, <=32 - the account/phone number that
                                   actually receives the money. Not optional in
                                   practice when 00 names an institution.
        02  acquiring bank         optional, <=32 - display name, e.g. "ABA Bank"

    Tag 99 carries both timestamps in ms: sub-00 creation, sub-01 expiry. The spec
    makes the expiry mandatory on dynamic codes, so it is always written - see
    KHQR_EXPIRY_MINUTES."""
    now_ms = int(time.time() * 1000)
    expires_ms = now_ms + settings.KHQR_EXPIRY_MINUTES * 60 * 1000

    account = _tlv("00", settings.BAKONG_ACCOUNT_ID[:32])
    if settings.BAKONG_ACCOUNT_INFORMATION:
        account += _tlv("01", settings.BAKONG_ACCOUNT_INFORMATION[:32])
    if settings.BAKONG_ACQUIRING_BANK:
        account += _tlv("02", settings.BAKONG_ACQUIRING_BANK[:32])

    payload = (
        _tlv("00", "01")
        + _tlv("01", "12")
        + _tlv("29", account)
        + _tlv("52", _MERCHANT_CATEGORY_CODE)
        + _tlv("53", _CURRENCY_USD)
        + _tlv("54", f"{amount:.2f}")
        + _tlv("58", "KH")
        + _tlv("59", settings.KHQR_MERCHANT_NAME[:25])
        + _tlv("60", settings.KHQR_MERCHANT_CITY[:15])
        + _tlv("62", _tlv("01", bill_number[:25]))
        + _tlv("99", _tlv("00", str(now_ms)) + _tlv("01", str(expires_ms)))
    )
    return payload + "6304" + _crc16_ccitt(payload + "6304")


def build_khqr(amount: Decimal, bill_number: str) -> tuple[str, str]:
    """Builds the dynamic KHQR payload for `amount` USD and returns (payload, md5).

    Prefers BAKONG_ACCOUNT_ID (see _build_from_account_id - a spec-shaped KHQR that
    routes over Bakong, so payments are confirmable), and only falls back to
    KHQR_STATIC_TEMPLATE when no account id is set. Raises RuntimeError if neither
    is configured - callers should have checked settings.khqr_configured first.
    `scripts/decode_khqr.py` renders either result back out field-by-field, which is
    the quickest way to eyeball one."""
    if settings.BAKONG_ACCOUNT_ID:
        payload = _build_from_account_id(amount, bill_number)
    elif settings.KHQR_STATIC_TEMPLATE:
        payload = _build_from_template(amount, bill_number)
    else:
        raise RuntimeError(
            "KHQR is not configured (set KHQR_STATIC_TEMPLATE or BAKONG_ACCOUNT_ID)"
        )
    return payload, hashlib.md5(payload.encode("utf-8")).hexdigest()


def khqr_expired(payload: str) -> bool:
    """True only when `payload` carries an expiry (tag 99 sub-01) that has passed.

    Anything we can't read an expiry out of - a PayWay-generated QR, one built
    before expiries were written, a malformed string - counts as NOT expired: the
    caller uses this to decide whether to mint a replacement, and needlessly
    replacing a QR a customer is mid-scan on is the worse failure."""
    try:
        fields = dict(_parse_tlv(payload))
        expires = dict(_parse_tlv(fields.get("99", ""))).get("01")
        if not expires:
            return False
        return int(expires) < int(time.time() * 1000)
    except (ValueError, KeyError):
        return False


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
