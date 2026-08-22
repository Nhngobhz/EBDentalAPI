"""
Composing the text of every outbound Telegram message.

This module holds the *words*; app/services/telegram.py holds the *sending*, and
app/routers/telegram_webhook.py holds the *reacting*. Splitting them is the point:
before this existed, six call sites each concatenated their own "\n"-joined string
and they had drifted apart - the same field was labelled "No:", "Quote No:" and
"Order No:" in three neighbouring messages, money was spaced two different ways,
and an internal column name (user_management) was being shown to staff.

Everything here is a pure function: no httpx, no database, no I/O at all. That keeps
it unit-testable without a network (see tests/test_telegram_format.py) and lets
core/logging_conf.py import it from inside a logging handler without dragging the
rest of the app in behind it.

Two Telegram limits shape the design (see LIMITS below): a document's caption is far
shorter than a plain message, and an order with thirty lines will not fit in one.
render_order_alert() therefore returns *both* a full and a compact rendering and lets
the sender decide - see OrderAlert.

**Money formatting is deliberately not invoice_pdf's.** That module prints "$ 12.00"
into a fixed-width column on a page; this one writes "$1,284.50" into a chat bubble,
with a thousands separator, because it is read at a glance on a phone. The *numbers*
must always agree between the two - the spacing need not, and a future quote-parity
pass should not "fix" this to match the PDF.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Iterable, Sequence
from urllib.parse import urlparse

if TYPE_CHECKING:
    from app.schemas import OrderItemOut, OrderOut


# --- Telegram's limits ------------------------------------------------------
# A document's caption is capped at 1024 and a plain sendMessage at 4096, both
# counted in UTF-16 code units *after* the HTML has been parsed into entities -
# so the tags themselves are free but every emoji outside the BMP costs two.
# visible_length() below measures exactly that rather than len().
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096

# What an order alert's caption is actually allowed to use. The slack is reserved for
# the "Status: Delivered ✅" line the webhook appends when a button is pressed: without
# it, a caption that only just fitted would have to fall back to the compact rendering
# on that edit, and the item list would appear to vanish the moment someone tapped it.
CAPTION_BUDGET = CAPTION_LIMIT - 48

# Cambodia is UTC+7 all year - no DST has ever applied - so a fixed offset is exactly
# correct here and, unlike ZoneInfo("Asia/Phnom_Penh"), needs no tzdata package on the
# Windows server (see DEPLOY_WINDOWS_SERVER.md).
ICT = timezone(timedelta(hours=7))

RULE = "──────────────────────"

_TAG_RE = re.compile(r"<[^>]+>")

# U+2212 MINUS SIGN, not a hyphen: at chat font sizes a hyphen in front of a number
# reads as a dash between two figures.
_MINUS = "−"


def esc(value: object) -> str:
    """Escape a value before it goes into a parse_mode=HTML message.

    Every message here is sent with parse_mode="HTML", and several interpolate free
    text the *customer* typed (clinic_name, address) or that a staff member chose
    (user_name, role_title). Telegram rejects a message whose entities don't parse -
    so a clinic literally named "Smith <Dental>" didn't produce a mangled alert, it
    produced NO alert (and the text-only fallback, built from the same caption, failed
    identically). Escaping keeps the alert deliverable whatever anyone types, and
    closes the matching injection of arbitrary markup into the staff chat.

    quote=False because these values land in element *content*. The one value that
    lands in an attribute instead - a map URL in an href - is escaped separately in
    maps_url()'s caller with quote=True."""
    return html.escape(str(value if value is not None else ""), quote=False)


def visible_length(text: str) -> int:
    """How long Telegram will consider this message, in the units it actually counts.

    Not len(): HTML tags are stripped out during parsing and cost nothing, while an
    emoji like 🧾 (outside the BMP) counts as two. Getting this wrong in the
    optimistic direction means Telegram rejects the whole alert."""
    plain = html.unescape(_TAG_RE.sub("", text))
    return len(plain.encode("utf-16-le")) // 2


def money(value) -> str:
    """"$1,284.50". See the module docstring on why this isn't invoice_pdf's format."""
    try:
        amount = Decimal(value)
    except (TypeError, ValueError, InvalidOperation):
        return "$0.00"
    if amount < 0:
        return f"{_MINUS}${abs(amount):,.2f}"
    return f"${amount:,.2f}"


def when(value: datetime | None) -> str:
    """"22 Aug 2:32 PM", in Cambodian local time.

    Timestamps are stored as timezone-aware UTC (every created_at column is
    DateTime(timezone=True)), and a staff member reading "07:32" for an order they
    watched being placed at 14:32 would reasonably conclude the alert was stale."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(ICT)
    hour = local.hour % 12 or 12
    return f"{local.day} {local:%b} {hour}:{local:%M} {local:%p}"


def maps_url(order) -> str | None:
    """The best "open this delivery address" URL for an order, or None.

    Mirrors location_link()/google_maps_url() in EB Web Project/maps.py: prefer the
    link the customer pasted (it often names a building or a business, which is more
    use to a driver than a bare coordinate pair) and fall back to a URL synthesized
    from the dropped pin. None when there is neither, so the caller renders no line
    rather than a dead one.

    The pasted link is re-validated here even though schemas.py::_validate_map_link
    refuses a bad one on the way in, because rows written before that validator
    existed are still in the table and this value goes straight into an href - the
    same reasoning as the Flask side's resolve_link_url(). The host allowlist is
    imported rather than restated: one definition of what counts as a map link.
    Imported lazily so core/logging_conf.py can use this module for error messages
    without pulling pydantic in through a logging handler."""
    from app.schemas import _MAP_LINK_HOST_RE

    link = (getattr(order, "map_link", None) or "").strip()
    if link:
        try:
            parsed = urlparse(link)
        except ValueError:
            parsed = None
        if (
            parsed is not None
            and parsed.scheme in ("http", "https")
            and _MAP_LINK_HOST_RE.match((parsed.hostname or "").lower())
        ):
            return link

    latitude = getattr(order, "latitude", None)
    longitude = getattr(order, "longitude", None)
    if latitude is None or longitude is None:
        return None
    try:
        # ?q= (rather than the /@ form) is what opens the place sheet with a marker
        # on it, in both the Google Maps app and the website.
        return f"https://www.google.com/maps?q={float(latitude):.6f},{float(longitude):.6f}"
    except (TypeError, ValueError):
        return None


# --- order alerts -----------------------------------------------------------

@dataclass(frozen=True)
class OrderAlert:
    """Both renderings of one order, plus the pieces the sender may need separately.

    `full` is what should be shown wherever it fits. `compact` is the same message
    with the item list removed, for when it doesn't - the sender then posts
    `items_message` as a reply underneath (see telegram.py::send_order_alert)."""

    full: str
    compact: str
    items_message: str

    def caption(self) -> str:
        """The rendering that fits in a document caption."""
        return self.full if visible_length(self.full) <= CAPTION_BUDGET else self.compact

    @property
    def needs_items_followup(self) -> bool:
        return visible_length(self.full) > CAPTION_BUDGET


def _headline(order) -> str:
    total = money(order.grand_total)
    if order.payment_status == "paid":
        via = " via KHQR" if order.payment_method == "khqr" else ""
        kind = "QUOTE" if order.order_type == "quote" else "ORDER"
        return f"✅ <b>{kind} PAID{via}</b> · <b>{esc(total)}</b>"
    if order.order_type == "quote":
        return f"🧾 <b>NEW QUOTE</b> · <b>{esc(total)}</b>"
    return f"🛍 <b>NEW ORDER</b> · <b>{esc(total)}</b>"


def _who_and_where(order) -> list[str]:
    """Clinic, who to call, where to deliver - the part staff actually act on, and
    the part the old alert left out entirely."""
    lines = [f"<b>{esc(order.clinic_name)}</b>"]

    contact_bits = []
    if order.contact_person:
        contact_bits.append(f"👤 {esc(order.contact_person)}")
    if order.phone:
        contact_bits.append(f"📞 {esc(order.phone)}")
    if contact_bits:
        lines.append(" · ".join(contact_bits))

    if order.address:
        lines.append(f"📍 {esc(order.address)}")
    url = maps_url(order)
    if url:
        # quote=True: this one is an attribute value, not element content.
        lines.append(f'🗺 <a href="{html.escape(url, quote=True)}">Open in Google Maps</a>')
    return lines


def _item_line(line_no: int, item: "OrderItemOut") -> str:
    """One numbered line. Component lines (a promotion/set's member products, a
    product's free gifts - see OrderItem.parent_item_id) are indented under the paid
    line above them and priced as "free".

    Numbered in one continuous run *including* components, matching
    invoice_pdf.py's `enumerate(order.items, start=1)` over the same flat list, so a
    line number read off the chat points at the same row on the printed quote."""
    qty = f"×{item.qty}"
    if item.uom:
        qty = f"{qty} {esc(item.uom)}"
    if item.parent_item_id is not None:
        return f"{line_no}. ↳ {esc(item.product_name)} {qty} — free"
    return f"{line_no}. {esc(item.product_name)} {qty} — {esc(money(item.line_amount))}"


def _item_lines(items: Sequence["OrderItemOut"]) -> list[str]:
    return [_item_line(n, item) for n, item in enumerate(items, start=1)]


def _totals(order) -> list[str]:
    """Only rendered when there is a discount to explain. Without one, subtotal and
    grand total are the same figure and the headline has already said it - repeating
    it twice more is the kind of noise this rewrite exists to remove."""
    discount = Decimal(order.discount_amount or 0)
    if not discount:
        return []
    return [
        f"Subtotal: {esc(money(order.subtotal))}",
        f"Discount: {esc(_MINUS + money(discount).lstrip(_MINUS))}",
        f"<b>Total: {esc(money(order.grand_total))}</b>",
    ]


def _footer(order) -> list[str]:
    """Identity and provenance, in the same order and wording for every variant."""
    label = "Quote" if order.order_type == "quote" else "Order"
    lines = [f"{label} {esc(order.order_number)} · Code {esc(order.quote_code)}"]

    trailing = [esc(order.salesperson or "-"), esc(when(order.created_at))]
    if order.payment_status != "paid" and order.payment_method == "cash":
        trailing.append("Cash to collect")
    lines.append(" · ".join(bit for bit in trailing if bit))

    # Gated on payment_status, never on paid_at: an order marked paid by hand at the
    # counter may carry no timestamp, and falling through to the unpaid notice because
    # of that would announce a completed sale as "no payment received".
    if order.payment_status == "paid":
        if order.paid_at:
            lines.append(f"💵 Paid {esc(when(order.paid_at))}")
    elif order.order_type == "quote":
        lines.append("ℹ️ <i>Quotation — no payment received yet.</i>")
    return lines


def _join(*blocks: Iterable[str]) -> str:
    """Blocks separated by a blank line, empty blocks dropped."""
    rendered = ["\n".join(block) for block in blocks if block]
    return "\n\n".join(part for part in rendered if part)


def render_order_alert(order: "OrderOut") -> OrderAlert:
    """The message announcing a quote, an order, or a payment.

    Three headlines, one body. Paid is checked FIRST, and on payment_status alone: a
    quote whose payment staff recorded at the counter is a completed sale, and
    announcing it as "no payment has been made" because order_type still says "quote"
    would be exactly backwards."""
    head = [_headline(order), RULE, *_who_and_where(order)]
    totals = _totals(order)
    footer = _footer(order)

    item_lines = _item_lines(order.items)
    items_block = [f"<b>Items ({len(item_lines)})</b>", *item_lines] if item_lines else []

    full = _join(head, items_block, totals, footer)
    compact = _join(
        head,
        [f"<b>{len(item_lines)} item{'s' if len(item_lines) != 1 else ''}</b> — listed below"]
        if item_lines
        else [],
        totals,
        footer,
    )
    items_message = _join(
        [f"<b>Items ({len(item_lines)})</b> · {esc(order.order_number)}"], item_lines
    )
    return OrderAlert(full=full, compact=compact, items_message=items_message)


def split_for_messages(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """`text` broken on line boundaries into pieces Telegram will accept.

    Only ever exercised by an order long enough that even its bare item list exceeds
    a whole message, which is rare - but silently losing the tail of a 90-line order
    would be worse than sending two messages."""
    if visible_length(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        candidate = current + [line]
        if current and visible_length("\n".join(candidate)) > limit:
            chunks.append("\n".join(current))
            current = [line]
        else:
            current = candidate
    if current:
        chunks.append("\n".join(current))
    return chunks


# --- everything else the bot says -------------------------------------------

def render_login(user_name: str, email: str, role_title: str, is_admin_level: bool) -> str:
    """A staff sign-in.

    `is_admin_level` is the user_management permission, but that column name means
    nothing to the people reading this chat - it decides the icon and the word, and
    is never printed."""
    icon = "🛡" if is_admin_level else "👤"
    kind = "Admin login" if is_admin_level else "Staff login"
    return (
        f"{icon} <b>{kind}</b> — {esc(user_name)}\n"
        f"{esc(role_title)} · {esc(email)}\n"
        f"<i>{esc(when(datetime.now(timezone.utc)))}</i>"
    )


def render_khqr_pending(reference: str, grand_total, customer_name: str) -> str:
    """A customer is standing at the QR. Deliberately carries no order number,
    because at this point there is no order - a customer KHQR purchase writes nothing
    until the payment is confirmed (see routers/orders.py::create_checkout).
    `reference` is the QR's bill number, which is what the payment will show up as at
    the bank if staff need to reconcile it by hand."""
    return (
        f"⏳ <b>PAYING BY KHQR</b> · <b>{esc(money(grand_total))}</b>\n"
        f"{RULE}\n"
        f"<b>{esc(customer_name)}</b>\n"
        f"Ref: <code>{esc(reference)}</code>\n\n"
        f"ℹ️ <i>No order exists yet — one is created, with its invoice, "
        f"once the payment lands.</i>"
    )


def render_pdf_missing_notice() -> str:
    """Appended to an order alert that had to go out without its document."""
    return (
        "⚠️ <i>The quotation PDF could not be attached — open the order in the "
        "admin Orders page to print it.</i>"
    )


def render_status_change(caption: str, label: str) -> str:
    """An existing alert's caption after Delivered/Cancelled was pressed."""
    return f"{caption}\n\n<b>Status: {esc(label)}</b>"


# How much of a traceback is worth pushing to a phone. The bottom frames are the ones
# naming the actual failure; the top of a FastAPI traceback is fifteen frames of
# middleware that are identical for every error in the app.
_TRACEBACK_TAIL_LINES = 14
_TRACEBACK_MAX_CHARS = 3000


def render_error(
    level: str, logger_name: str, summary: str, traceback_text: str | None = None
) -> str:
    """An error pushed to the error topic.

    The old version sent `asctime | LEVEL | name | message` plus the entire traceback
    into one <pre>, truncated at a flat 3500 characters - which routinely cut through
    the middle of a stack frame and buried the one line naming the fault under the
    middleware frames above it. Header, cause, then the tail of the traceback."""
    text = (
        f"🚨 <b>{esc(level)}</b> · <code>{esc(logger_name)}</code>\n"
        f"{esc(summary.strip())[:1200]}"
    )
    if traceback_text:
        lines = traceback_text.strip().split("\n")
        clipped = lines[-_TRACEBACK_TAIL_LINES:]
        body = "\n".join(clipped)[-_TRACEBACK_MAX_CHARS:]
        ellipsis = "…\n" if len(lines) > _TRACEBACK_TAIL_LINES else ""
        text += f"\n\n<pre>{ellipsis}{esc(body)}</pre>"
    return text
