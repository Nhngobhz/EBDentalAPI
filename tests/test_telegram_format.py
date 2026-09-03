"""
Unit tests for app/services/telegram_format.py.

No fixtures, no database, no network: everything under test is a pure function that
turns an OrderOut into a string. The point of the module is that these can be checked
without a Telegram bot token, which the previous inline caption-building could not.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.schemas import OrderItemOut, OrderOut
from app.services.telegram_format import (
    CAPTION_BUDGET,
    CAPTION_LIMIT,
    MESSAGE_LIMIT,
    maps_url,
    money,
    render_error,
    render_login,
    render_order_alert,
    render_refund,
    render_status_change,
    split_for_messages,
    visible_length,
    when,
)

UTC = timezone.utc


def make_item(name="Dental Chair X3", qty=1, amount="980.00", parent=None, uom="PCS", item_id=1):
    return OrderItemOut(
        id=item_id,
        product_id=item_id,
        parent_item_id=parent,
        product_name=name,
        product_code=f"P{item_id:04d}",
        uom=uom,
        unit_price=Decimal(amount),
        list_price=Decimal(amount),
        discount=Decimal("0"),
        qty=qty,
        line_amount=Decimal(amount) * qty,
    )


def make_order(**overrides):
    defaults = dict(
        id=1,
        order_number="25-0813",
        quote_code="260822143201",
        clinic_name="Sunrise Dental Clinic",
        contact_person="Dr. Sok Chan",
        phone="012 345 678",
        address="St. 271, Phnom Penh",
        salesperson="Thay",
        discount_type="percent",
        discount_value=Decimal("0"),
        discount_amount=Decimal("0"),
        subtotal=Decimal("980.00"),
        grand_total=Decimal("980.00"),
        status="pending",
        order_type="quote",
        payment_method="cash",
        payment_status="unpaid",
        created_at=datetime(2026, 8, 22, 7, 32, tzinfo=UTC),
        updated_at=datetime(2026, 8, 22, 7, 32, tzinfo=UTC),
        items=[make_item()],
    )
    defaults.update(overrides)
    return OrderOut(**defaults)


# --- primitives -------------------------------------------------------------

def test_money_uses_a_thousands_separator_and_no_space():
    assert money(Decimal("1284.5")) == "$1,284.50"
    assert money(Decimal("7")) == "$7.00"
    assert money(None) == "$0.00"


def test_money_renders_a_negative_with_a_real_minus_sign():
    assert money(Decimal("-67.5")) == "−$67.50"


def test_when_converts_utc_to_cambodian_local_time():
    """07:32 UTC is 14:32 in Phnom Penh - the whole reason this helper exists."""
    assert when(datetime(2026, 8, 22, 7, 32, tzinfo=UTC)) == "22 Aug 2:32 PM"


def test_when_treats_a_naive_timestamp_as_utc():
    assert when(datetime(2026, 8, 22, 7, 32)) == "22 Aug 2:32 PM"


def test_when_can_roll_into_the_next_local_day():
    assert when(datetime(2026, 8, 22, 23, 0, tzinfo=UTC)) == "23 Aug 6:00 AM"


def test_visible_length_ignores_tags_and_counts_emoji_as_utf16():
    assert visible_length("<b>abc</b>") == 3
    # A non-BMP emoji is a surrogate pair, which is how Telegram counts it too.
    assert visible_length("\U0001F9FE") == 2
    assert visible_length("&lt;b&gt;") == 3


# --- maps -------------------------------------------------------------------

def test_maps_url_prefers_the_pasted_link_over_the_pin():
    order = make_order(
        map_link="https://maps.app.goo.gl/abc123",
        latitude=Decimal("11.556400"),
        longitude=Decimal("104.928200"),
    )
    assert maps_url(order) == "https://maps.app.goo.gl/abc123"


def test_maps_url_synthesizes_a_link_from_a_dropped_pin():
    order = make_order(latitude=Decimal("11.5564"), longitude=Decimal("104.9282"))
    assert maps_url(order) == "https://www.google.com/maps?q=11.556400,104.928200"


def test_maps_url_is_none_without_a_link_or_a_pin():
    assert maps_url(make_order()) is None


@pytest.mark.parametrize(
    "bad_link",
    ["javascript:alert(1)", "https://evil.example/maps", "http://google.com.evil.test/"],
)
def test_maps_url_rejects_a_link_that_predates_the_schema_validator(bad_link):
    """Rows written before schemas.py::_validate_map_link existed are still in the
    table, and this value goes straight into an href. OrderOut.map_link is a plain
    Optional[str] on the way out - the allowlist is only enforced on input - so this
    is exactly what such a row looks like by the time it reaches the renderer."""
    order = make_order(
        map_link=bad_link, latitude=Decimal("11.5564"), longitude=Decimal("104.9282")
    )
    assert maps_url(order) == "https://www.google.com/maps?q=11.556400,104.928200"


# --- order alerts -----------------------------------------------------------

def test_alert_carries_contact_address_and_a_maps_link():
    order = make_order(latitude=Decimal("11.5564"), longitude=Decimal("104.9282"))
    text = render_order_alert(order).full
    assert "Sunrise Dental Clinic" in text
    assert "Dr. Sok Chan" in text
    assert "012 345 678" in text
    assert "St. 271, Phnom Penh" in text
    assert 'href="https://www.google.com/maps?q=11.556400,104.928200"' in text


def test_new_quote_headline_and_unpaid_notice():
    text = render_order_alert(make_order()).full
    assert "NEW QUOTE" in text
    assert "$980.00" in text
    assert "no payment received yet" in text
    assert "Cash to collect" in text


def test_paid_headline_wins_over_order_type():
    """A quote whose payment staff recorded at the counter is a completed sale -
    announcing it as "no payment" because order_type still says "quote" would be
    exactly backwards."""
    order = make_order(
        order_type="quote",
        payment_status="paid",
        payment_method="khqr",
        paid_at=datetime(2026, 8, 22, 8, 0, tzinfo=UTC),
    )
    text = render_order_alert(order).full
    assert "QUOTE PAID via KHQR" in text
    assert "no payment received yet" not in text
    assert "Paid 22 Aug 3:00 PM" in text


def test_a_paid_quote_with_no_timestamp_still_reads_as_paid():
    """Marking an order paid at the counter doesn't always record paid_at. Gating the
    unpaid notice on that instead of on payment_status announced a completed sale as
    "no payment received yet"."""
    text = render_order_alert(make_order(payment_status="paid", paid_at=None)).full
    assert "QUOTE PAID" in text
    assert "no payment received yet" not in text
    assert "Cash to collect" not in text


def test_a_refunded_order_is_never_announced_as_paid():
    """A refunded row still carries paid_at and a payment_method, so a headline picked
    on those would call money that went back a completed sale. Both dates are kept -
    the payment is what a bank statement still shows, the refund is what undid it."""
    order = make_order(
        order_type="order",
        payment_status="refunded",
        payment_method="khqr",
        paid_at=datetime(2026, 8, 22, 8, 0, tzinfo=UTC),
        refunded_at=datetime(2026, 9, 2, 3, 0, tzinfo=UTC),
        refund_reason="Wrong item supplied",
    )
    text = render_order_alert(order).full
    assert "REFUNDED" in text
    assert "PAID" not in text
    assert "no payment received yet" not in text
    assert "Paid 22 Aug 3:00 PM" in text
    assert "Refunded 2 Sep 10:00 AM" in text
    assert "Wrong item supplied" in text


def test_render_refund_names_the_order_and_the_amount():
    """The standalone alert fired when the money goes back. Short on purpose: the
    invoice already went to this chat when the payment landed."""
    order = make_order(
        payment_status="refunded",
        refunded_at=datetime(2026, 9, 2, 3, 0, tzinfo=UTC),
        refund_reason="Customer returned it",
    )
    text = render_refund(order)
    assert "REFUNDED" in text
    assert "$980.00" in text
    assert "25-0813" in text
    assert "Sunrise Dental Clinic" in text
    assert "Reason: Customer returned it" in text


def test_render_refund_escapes_a_reason_somebody_typed():
    """refund_reason is free text off an admin form and this string is sent with
    parse_mode=HTML - unescaped, a stray "<" breaks the message Telegram rejects."""
    order = make_order(payment_status="refunded", refund_reason="<b>oops</b>")
    text = render_refund(order)
    assert "&lt;b&gt;oops&lt;/b&gt;" in text


def test_plain_order_headline():
    text = render_order_alert(make_order(order_type="order", payment_method="khqr")).full
    assert "NEW ORDER" in text


def test_totals_block_only_appears_when_there_is_a_discount():
    assert "Subtotal" not in render_order_alert(make_order()).full
    discounted = make_order(
        subtotal=Decimal("1352.00"),
        discount_amount=Decimal("67.50"),
        grand_total=Decimal("1284.50"),
    )
    text = render_order_alert(discounted).full
    assert "Subtotal: $1,352.00" in text
    assert "Discount: −$67.50" in text
    assert "Total: $1,284.50" in text


def test_components_are_nested_and_share_one_numbering_run():
    """Mirrors invoice_pdf.py's enumerate() over the same flat list, so a line number
    read off the chat points at the same row on the printed quote."""
    order = make_order(
        items=[
            make_item(item_id=1),
            make_item(name="Applicator Tips", qty=2, amount="0.00", parent=1, item_id=2),
            make_item(name="Curing Light", qty=2, amount="76.13", item_id=3),
        ]
    )
    text = render_order_alert(order).full
    assert "Items (3)" in text
    assert "1. Dental Chair X3 ×1 PCS — $980.00" in text
    assert "2. ↳ Applicator Tips ×2 PCS — free" in text
    assert "3. Curing Light ×2 PCS — $152.26" in text


def test_a_clinic_name_containing_markup_is_escaped_not_dropped():
    """A clinic literally named "Smith <Dental>" used to produce NO alert at all:
    Telegram rejects a message whose entities don't parse."""
    text = render_order_alert(make_order(clinic_name="Smith <Dental> & Co")).full
    assert "Smith &lt;Dental&gt; &amp; Co" in text
    assert "<Dental>" not in text


# --- the caption limit ------------------------------------------------------

def big_order(lines=40):
    return make_order(
        items=[
            make_item(name=f"Composite Restorative Kit A{n}", qty=n, amount="12.50", item_id=n)
            for n in range(1, lines + 1)
        ]
    )


def test_a_short_order_fits_in_one_caption_with_its_items():
    alert = render_order_alert(make_order())
    assert not alert.needs_items_followup
    assert alert.caption() == alert.full
    assert "Items (1)" in alert.caption()


def test_a_long_order_falls_back_to_the_compact_caption():
    alert = render_order_alert(big_order())
    assert alert.needs_items_followup
    assert alert.caption() == alert.compact
    assert visible_length(alert.compact) <= CAPTION_BUDGET
    assert "<b>40 items</b> — listed below" in alert.compact
    # The clinic, the money and the identity survive; only the itemisation moves.
    assert "Sunrise Dental Clinic" in alert.compact
    assert "Quote 25-0813" in alert.compact


def test_the_followup_message_carries_every_line():
    alert = render_order_alert(big_order())
    assert "Items (40)" in alert.items_message
    assert "40. Composite Restorative Kit A40" in alert.items_message


def test_a_caption_leaves_room_for_the_status_line_appended_on_a_button_press():
    """A caption that only just fitted must not have to shed its items when someone
    taps Delivered - that reads as the message losing content."""
    alert = render_order_alert(big_order(lines=11))
    edited = render_status_change(alert.caption(), "Delivered ✅")
    assert visible_length(edited) <= CAPTION_LIMIT


def test_split_for_messages_breaks_on_line_boundaries():
    text = "\n".join(f"line {n}" for n in range(1, 2001))
    chunks = split_for_messages(text)
    assert len(chunks) > 1
    assert all(visible_length(chunk) <= MESSAGE_LIMIT for chunk in chunks)
    assert "\n".join(chunks) == text


def test_split_for_messages_leaves_a_short_message_alone():
    assert split_for_messages("hello") == ["hello"]


# --- the other messages -----------------------------------------------------

def test_login_message_never_prints_the_permission_column_name():
    admin = render_login("Thay Bunthai", "thay@example.com", "Administrator", True)
    assert "Admin login" in admin
    assert "user_management" not in admin
    assert "Administrator · thay@example.com" in admin

    staff = render_login("Sok Dara", "dara@example.com", "Sales", False)
    assert "Staff login" in staff
    assert "Admin login" not in staff


def test_render_error_keeps_the_tail_of_a_traceback():
    tb = "\n".join(f"  frame {n}" for n in range(1, 41)) + "\nValueError: boom"
    text = render_error("ERROR", "app.main", "Unhandled exception on POST /orders/", tb)
    assert "app.main" in text
    assert "Unhandled exception on POST /orders/" in text
    assert "ValueError: boom" in text
    # The middleware frames at the top are dropped, not the cause at the bottom.
    assert "frame 1\n" not in text
    assert "…" in text


def test_render_error_escapes_a_message_containing_markup():
    text = render_error("ERROR", "app.orders", "bad value <object at 0x1> passed")
    assert "&lt;object at 0x1&gt;" in text
    assert "<pre>" not in text  # no traceback given, so no code block
