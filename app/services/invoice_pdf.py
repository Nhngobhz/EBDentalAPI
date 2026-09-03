"""
Server-side quotation PDF, used as the Telegram order-alert attachment (see
send_order_alert() in app/services/telegram.py). Deliberately rebuilt (2026-07-22) to
mirror the layout of the official customer-facing quotation PDF, which is built
client-side in the EB Web Project's main.js (QuoteCart.buildPrintTemplate/exportPDF) via
html2canvas - see that file if you're changing what the printed quote looks like, then
mirror the change here too so the two stay in sync.

Two bundled fonts (app/assets/fonts/), matching the website print template's own font
stack exactly (`.quote-print-template`'s default is Inter, `.qpt-khmer` elements -
Clinic/Address values, the signature-strip captions - switch to Noto Sans Khmer):
- Inter, for everything else (header, labels, table, totals).
- Noto Sans Khmer, for real Khmer glyph support. fpdf2's font subsetter can't subset a
  variable font directly (raises KeyError: 'fvar'), so both fonts' original variable
  builds (google/fonts' ofl/notosanskhmer and ofl/inter) were each instanced to static
  Regular/Bold TTFs with fontTools.varLib.instancer before being added here. The
  OFL.txt files alongside them are the fonts' SIL Open Font Licenses, kept for
  attribution.

Known limitation: with Khmer text shaping enabled (needed for correct subscript/vowel
rendering - see _use_khmer_font), fpdf2 occasionally emits a slightly-off ToUnicode
CMap entry for certain glyph clusters (e.g. "ដោយ"), so copy-pasting text out of the
Khmer-font portions of the PDF can come out with a stray extra character even though
the rendered glyphs themselves are correct. This is a narrow fpdf2/HarfBuzz-shaping
bug, not something introduced here - only affects text extraction/searchability, never
what's actually printed/displayed.
"""
import io
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
from fpdf import FPDF
from fpdf.enums import TableBordersLayout
from fpdf.fonts import FontFace

from app.core.logging_conf import get_logger
from app.schemas import OrderOut
from app.services import app_settings

logger = get_logger("invoice_pdf")

_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
# Mirrors the website print template's font stack exactly: 'Inter' is the default
# (.quote-print-template), 'Noto Sans Khmer' only kicks in on elements explicitly
# tagged .qpt-khmer in main.js's buildPrintTemplate (Clinic/Address values, the
# signature-strip captions) - everything else (labels, table, totals) uses Inter, same
# as the website. Text shaping is only turned on around Khmer-font text (see
# _use_khmer_font/_use_latin_font below) - Inter is Latin-only and doesn't need it.
_LATIN_FONT = "Inter"
_KHMER_FONT = "NotoKhmer"


def _use_latin_font(pdf, size, bold=False):
    pdf.set_text_shaping(False)
    pdf.set_font(_LATIN_FONT, "B" if bold else "", size)


def _use_khmer_font(pdf, size, bold=False):
    pdf.set_text_shaping(True)
    pdf.set_font(_KHMER_FONT, "B" if bold else "", size)


def _has_khmer(text) -> bool:
    """Product name/code are plain free text, same as the website's Description column
    (never tagged .qpt-khmer there either) - but unlike the browser, fpdf2 doesn't
    auto-fall-back to a different font for glyphs Inter can't draw, so a product name
    that happens to contain real Khmer script needs to be detected and rendered with
    the Khmer font explicitly, or its characters would just be missing/blank."""
    return any("ក" <= ch <= "៿" for ch in str(text or ""))


# The line's price BEFORE its discount, read from the snapshot rather than
# reconstructed. This used to divide the discount back out of unit_price, in a copy
# of the same arithmetic that lived in main.js and formatting.py - all three had to
# agree, and the figure moved whenever a price was edited. OrderItem.list_price now
# stores it outright (see store-api's f2a9c4e18b73 migration); main.js's
# deriveOldUnitPrice() reads the same field for the client-rendered PDF this one
# stands in for.
def _old_unit_price(item) -> Decimal:
    unit_price = Decimal(item.unit_price)
    list_price = Decimal(item.list_price or 0)
    return list_price if list_price > unit_price else unit_price


def _format_plain_number(value: Decimal) -> str:
    """Decimal.normalize() can flip a round number like 10 into scientific notation
    ("1E+1") - format as a fixed-point string and trim trailing zeros instead, matching
    what main.js's `Number(discount) + '%'` prints (e.g. "10%", "12.5%")."""
    s = f"{Decimal(value):f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _format_item_discount(discount: Decimal, discount_type: str) -> str:
    # Only a % discount shows inline on its own row - a $ (cash) discount is rolled into
    # the combined "Discount($)" total row instead (see _item_display_amount below),
    # mirroring printedItemDiscountText()/printedItemAmount() in main.js.
    if discount_type != "percent" or not discount:
        return "—"  # em dash, matches the website's "—" placeholder
    return f"{_format_plain_number(discount)}%"


def _item_display_amount(old_unit_price: Decimal, line_amount, qty, discount_type: str) -> Decimal:
    if discount_type == "cash":
        return old_unit_price * qty
    return Decimal(line_amount)


def _money(value) -> str:
    return f"$ {Decimal(value):.2f}"


# The payment-QR block in the terms box at the foot of a quotation. Sizes in mm, since
# that is the unit build_invoice_pdf() works in. 24mm is about what the paper original
# prints and comfortably above the ~15mm where a phone camera starts to struggle.
_QR_SIZE_MM = 24
_QR_CAPTION_MM = 4  # the account-name line under the QR
_QR_GUTTER_MM = 3  # gap kept clear between the wrapped terms text and the QR


def _payment_qr_image(url: str):
    """The configured payment QR as a file-like object fpdf2 can place, or None.

    The stored value is an ordinary image field (app/core/files.py): a full R2 URL, or
    a "/static/uploads/..." path this same container serves off its own disk.

    Every failure returns None rather than raising. This runs inside the Telegram
    order alert, and a QR that 404s must not be the reason a customer's quotation
    never gets built - the document simply prints without it.
    """
    url = (url or "").strip()
    if not url:
        return None
    try:
        if url.startswith(("http://", "https://")):
            response = httpx.get(url, timeout=5.0, follow_redirects=True)
            response.raise_for_status()
            return io.BytesIO(response.content)
        # Local-disk fallback: the leading "/" is the URL path, not a filesystem root -
        # UPLOAD_DIR is relative to the process's working directory (see storage.py).
        return io.BytesIO(Path(url.lstrip("/")).read_bytes())
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("Could not load the quotation payment QR from %r: %s", url, exc)
        return None


CANCELLED_NOTE = "This order was cancelled. It is not an invoice and is not payable."
REFUNDED_NOTE = "This invoice has been refunded. The payment was returned to the customer."


def document_title(order) -> str:
    """What the printed document calls itself. Three outcomes, checked in this order:

    1. **A cancelled order is never an invoice**, whatever its payment state - it prints
       as "Cancelled Order". A cancelled sale that had been paid is money owed back, and
       a page headed "Invoice" is a claim that a sale stands; handing one to a customer
       (or filing it) misstates the position. Storefront downloads for a cancelled order
       are removed entirely - this title is the backstop for the paths that still build
       one, chiefly the Telegram alert fired when a payment confirms against an order
       staff had already cancelled.
    2. Anything the system has recorded a payment for is an **Invoice** - a confirmed
       KHQR payment, or a quote staff marked paid after taking cash at the counter. Keyed
       on payment_status, NOT on order_type: a paid quote IS the sale. A **refunded**
       row keeps that title: the invoice was really issued and is what the refund was
       made against, so re-printing it as a "Quotation" would deny it ever existed. The
       reversal is stated in the terms box instead (REFUNDED_NOTE below).
    3. Everything else is a **Quotation**.

    (2) was called "Receipt" until 2026-08-17, renamed on the owner's instruction - a paid
    quote becomes that sale's invoice in how this business talks about it, and the word
    the customer is handed should match. Only the printed word changed: `receipt_note_khqr`
    / `receipt_note_cash` keep their setting keys, since renaming a key would strand
    whatever wording the admin had saved under the old one.

    Public (no underscore) because it is the one place this rule is written down on the
    server: telegram.py names the attached file from it too. The client-side mirror is
    `docTitle` in QuoteCart.buildPrintTemplate() - change one, change both.
    """
    if getattr(order, "status", None) == "cancelled":
        return "Cancelled Order"
    settled = getattr(order, "payment_status", None) in ("paid", "refunded")
    return "Invoice" if settled else "Quotation"


class _QuotePDF(FPDF):
    def header(self):
        pass

    def footer(self):
        pass


def build_invoice_pdf(order: OrderOut) -> bytes:
    # The letterhead and the validity/paid wording are admin-editable (the Settings
    # screen's "Quote & Invoice" group). This runs outside a request - it's called from
    # the Telegram service - so get_all() opens its own short-lived session; see
    # app/services/app_settings.py. The same keys drive buildPrintTemplate() in the
    # website's main.js, and the two must stay in step.
    site = app_settings.get_all()

    pdf = _QuotePDF(unit="mm", format="A4")
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_font(_KHMER_FONT, "", str(_FONTS_DIR / "NotoSansKhmer-Regular.ttf"))
    pdf.add_font(_KHMER_FONT, "B", str(_FONTS_DIR / "NotoSansKhmer-Bold.ttf"))
    pdf.add_font(_LATIN_FONT, "", str(_FONTS_DIR / "Inter-Regular.ttf"))
    pdf.add_font(_LATIN_FONT, "B", str(_FONTS_DIR / "Inter-Bold.ttf"))
    _use_latin_font(pdf, 9)
    pdf.add_page()

    content_width = pdf.w - pdf.l_margin - pdf.r_margin  # 190mm at A4/10mm margins
    top = pdf.get_y()

    doc_title = document_title(order)
    is_cancelled = order.status == "cancelled"
    # A cancelled row prints neither the paid note ("Thank you for your purchase") nor a
    # validity line - both would be untrue - so it gets its own literal instead. Not a
    # setting: it states a fact about the row rather than wording the shop chooses, and
    # a settings page full of knobs nobody turns is its own problem (settings_spec.py).
    is_paid_document = order.payment_status == "paid" and not is_cancelled
    # A refunded invoice is neither payable nor a completed sale, so it gets neither the
    # thank-you note nor the bank QR - it says what happened to it instead.
    is_refunded = order.payment_status == "refunded" and not is_cancelled
    paid_note = (
        site["receipt_note_khqr"]
        if order.payment_method == "khqr"
        else site["receipt_note_cash"]
    )
    # The terms box at the foot of the item table. A quotation carries the shop's
    # standing terms and the bank QR to pay against; a paid invoice and a cancelled
    # order each carry one line saying so and no QR - "please scan to pay" on a document
    # that is already settled, or void, reads as a mistake. Mirrored by the
    # validityNote/termsLines block in buildPrintTemplate() in the website's main.js.
    if is_paid_document:
        terms_lines = [paid_note]
    elif is_refunded:
        refunded_on = order.refunded_at.strftime("%d %b %Y") if order.refunded_at else ""
        terms_lines = [REFUNDED_NOTE + (f" ({refunded_on})" if refunded_on else "")]
        if order.refund_reason:
            terms_lines.append(f"Reason: {order.refund_reason}")
    elif is_cancelled:
        terms_lines = [CANCELLED_NOTE]
    else:
        terms_lines = [
            line for line in (
                f"Quotation is valid for {site['quote_validity_days']} days from the date issued.",
                site["quote_deposit_note"],
                site["quote_payment_note"],
            ) if line
        ]
    qr_image = None if (is_paid_document or is_refunded or is_cancelled) else _payment_qr_image(
        site["quote_payment_qr"]
    )
    qr_caption = site["quote_payment_qr_caption"] if qr_image is not None else ""

    # ---- header: brand (left) / the document title + No/Date (right) ----
    # Font sizes/positions mirror qpt-brand-name/qpt-title (1.6-1.7rem) and
    # qpt-brand-meta/qpt-meta-right (0.72-0.75rem) at ~96dpi/16px-root.
    pdf.set_xy(pdf.l_margin, top)
    _use_latin_font(pdf, 20, bold=True)
    pdf.cell(content_width / 2, 9, site["document_brand_name"])
    pdf.set_xy(pdf.l_margin + content_width / 2, top)
    pdf.cell(content_width / 2, 9, doc_title, align="R")

    pdf.set_xy(pdf.l_margin, top + 9)
    _use_latin_font(pdf, 8.5)
    # Two lines when a phone number is set, one when it isn't - an empty "Tel:" prefix
    # would otherwise print on its own.
    tel_line = site["document_tel_line"]
    pdf.multi_cell(
        content_width / 2,
        4.2,
        f"{site['document_address_line']}\nTel: {tel_line}" if tel_line
        else site["document_address_line"],
    )

    created = order.created_at or datetime.now(timezone.utc)
    pdf.set_xy(pdf.l_margin + content_width / 2, top + 9)
    pdf.multi_cell(
        content_width / 2, 4.2,
        f"No : {order.order_number}\nDate: {created.strftime('%d/%m/%Y')}",
        align="R",
    )

    # border-bottom: 2px solid + padding-bottom: 14px + margin-bottom: 16px on
    # .qpt-header - the divider line between the brand block and the info block.
    header_bottom = max(pdf.get_y(), top + 21)
    line_y = header_bottom + 3.7
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, line_y, pdf.l_margin + content_width, line_y)
    pdf.set_line_width(0.2)
    pdf.set_y(line_y + 4.2)

    # ---- info block: two columns of label/value rows, mirrors qpt-info-block ----
    info_top = pdf.get_y()
    col_width = content_width / 2
    label_width = 32

    def info_rows(x, rows, khmer_fields=()):
        # Address (and potentially Clinic) can be long enough to wrap - a plain fixed-
        # height cell() doesn't wrap at all, it just overflows past the column width and
        # bleeds into the neighboring column's text. Measure the wrapped line count with
        # a dry run first so the row - and everything below it in this column - can grow
        # to fit instead of overlapping.
        y = info_top
        value_width = col_width - label_width
        for label, value in rows:
            text = str(value or "—")
            if label in khmer_fields:
                _use_khmer_font(pdf, 9, bold=True)
            else:
                _use_latin_font(pdf, 9, bold=True)
            lines = pdf.multi_cell(value_width, 5, text, align="L", dry_run=True, output="LINES")
            row_height = 5 * max(1, len(lines))

            pdf.set_xy(x, y)
            _use_latin_font(pdf, 9)
            pdf.cell(label_width, row_height, label)

            if label in khmer_fields:
                _use_khmer_font(pdf, 9, bold=True)
            else:
                _use_latin_font(pdf, 9, bold=True)
            pdf.set_xy(x + label_width, y)
            pdf.multi_cell(value_width, 5, text, align="L")

            y += row_height
        return y

    # Clinic/Address are the two fields the website tags .qpt-khmer (main.js's
    # buildPrintTemplate) - free-text customer input that may contain real Khmer script.
    left_bottom = info_rows(pdf.l_margin, [
        ("C. Code", order.quote_code),
        ("Clinic", order.clinic_name),
        ("Contact Tel", order.phone),
        ("Address", order.address),
    ], khmer_fields=("Clinic", "Address"))
    right_bottom = info_rows(pdf.l_margin + col_width, [
        # A staff-written term wins; the standing ones are the fallback. Same two
        # settings the Flask app substitutes onto a customer's order in
        # blueprints/quote.py, so the printed document and the recorded order agree.
        ("Payment Term", order.payment_term or site["default_payment_term"]),
        ("Salesperson", order.salesperson),
        ("User", order.quoted_by_name),
        ("Installation Term", order.install_term or site["default_install_term"]),
        ("Contact Person", order.contact_person or site["default_contact_person"]),
    ])
    pdf.set_y(max(left_bottom, right_bottom) + 4)

    # ---- item table: No / Code / Description / Qty / UOM / UP / Discount / Amount ----
    # UP shows the reconstructed UNDISCOUNTED unit price (mirrors the website PDF) -
    # Amount is still qty x the actually-charged unit_price, so it always reconciles
    # with order.subtotal/grand_total.
    # The last two columns double as the totals block's label/value pair (see below) -
    # sized wide enough for "Special Discount (100%):" to fit on one line at 8pt.
    col_widths = (8, 18, 50, 10, 12, 24, 38, 30)
    _use_latin_font(pdf, 8)

    # The terms cell spans the first six columns, and the QR is drawn into the right
    # end of it AFTER the table (fpdf2 cells hold either text or an image, never both).
    # Nothing stops a cell's own text from wrapping the full width and running straight
    # under that picture - so the lines are wrapped HERE, against the narrower width the
    # QR actually leaves, and handed to the cell already broken. Measured with the same
    # font and size the table is about to render in, which is why this sits after
    # _use_latin_font above.
    terms_width = sum(col_widths[:6])
    _CELL_PADDING = 1.2  # the table's `padding=` below, per side
    text_width = terms_width - 2 * _CELL_PADDING
    if qr_image is not None:
        text_width -= _QR_SIZE_MM + _QR_GUTTER_MM
    terms_text = "\n".join(
        line
        for source in terms_lines
        for line in pdf.multi_cell(text_width, 5, source, dry_run=True, output="LINES")
    )

    khmer_style = FontFace(family=_KHMER_FONT)
    undiscounted_subtotal = Decimal("0")
    # Only $ (cash) item discounts feed the "Discount($)" total row - a % item discount
    # is already visible inline on its own row (see _format_item_discount), so folding it
    # into this aggregate too would double-count the same discount.
    cash_discount_total = Decimal("0")
    item_rows = []
    # Component lines (a promotion/set's member products, a product's free
    # gifts - see OrderItem.parent_item_id) are $0 sub-lines of the paid line
    # above them: their price columns read "Free"/$0.00. They are still numbered,
    # in one run with the paid lines (2026-08-20), so every physical item on the
    # document can be counted off - hence enumerate over the flat list rather than
    # a counter that only advances on priced rows. Mirrors buildPrintTemplate in
    # main.js.
    for line_no, item in enumerate(order.items, start=1):
        is_component = getattr(item, "parent_item_id", None) is not None
        old_unit_price = _old_unit_price(item)
        undiscounted_subtotal += old_unit_price * item.qty
        if item.discount_type == "cash":
            cash_discount_total += (old_unit_price - Decimal(item.unit_price)) * item.qty
        code = item.product_code or "—"
        # Product code/name are plain free text - unlike Clinic/Address, the website
        # doesn't tag this column .qpt-khmer either, but a browser still auto-falls-back
        # per-glyph to a font that has it, which fpdf2 won't do on its own - so any cell
        # that actually contains Khmer script gets the Khmer font+shaping explicitly.
        display_amount = _item_display_amount(old_unit_price, item.line_amount, item.qty, item.discount_type)
        description = f"    • {item.product_name}" if is_component else item.product_name
        discount_text = "—" if is_component else _format_item_discount(item.discount, item.discount_type)
        item_rows.append((
            (str(line_no), None), (code, khmer_style if _has_khmer(code) else None),
            (description, khmer_style if _has_khmer(item.product_name) else None),
            (str(item.qty), None), (item.uom or "PCS", None), (_money(old_unit_price), None),
            (discount_text, None), (_money(display_amount), None),
        ))

    item_discount_total = cash_discount_total
    special_discount_label = (
        "Special Discount (Cash):" if order.discount_type == "cash"
        else f"Special Discount ({_format_plain_number(order.discount_value)}%):"
    )

    # Pads the table with blank rows so it always looks like a full, pre-printed form
    # (like the paper original) even when there are only a few items - mirrors
    # MIN_TABLE_ROWS in main.js's buildPrintTemplate.
    MIN_TABLE_ROWS = 21
    blank_rows_needed = max(0, MIN_TABLE_ROWS - len(item_rows))

    # Shaping only needs to be on for the table at all if some cell actually needs the
    # Khmer font - plain Latin/numeric cells (the overwhelming common case) render fine
    # and avoid the shaping engine's rare ToUnicode quirk (see module docstring) when
    # nothing here needs it.
    if any(style is not None for row in item_rows for _, style in row):
        pdf.set_text_shaping(True)

    with pdf.table(
        col_widths=col_widths,
        first_row_as_headings=True,
        num_heading_rows=1,
        text_align=("CENTER", "LEFT", "LEFT", "CENTER", "CENTER", "RIGHT", "CENTER", "RIGHT"),
        borders_layout=TableBordersLayout.ALL,
        line_height=5,
        padding=1.2,
    ) as table:
        # ONE heading row. This used to be two: every other column carried rowspan=2 and
        # a second row of empty filler cells sat under the "UP before & After Discount"
        # colspan header, purely to fill out the grid. Even with the shared borders
        # suppressed it still drew a divider down the lower half of that header cell,
        # which is the stray line the owner asked to be rid of (2026-08-20). The website's
        # own builder (buildPrintTemplate in main.js) dropped the same filler row, so the
        # two documents still match line for line.
        head = table.row()
        head.cell("No.")
        head.cell("Code")
        head.cell("Description")
        head.cell("Qty")
        head.cell("UOM")
        head.cell("UP before & After Discount", colspan=2)
        head.cell("Amount")

        for cells in item_rows:
            row = table.row()
            for text, style in cells:
                row.cell(text, style=style)

        for _ in range(blank_rows_needed):
            row = table.row()
            for _ in col_widths:
                row.cell("")

        totals_row = table.row()
        totals_row.cell(terms_text, colspan=6, rowspan=4, align="L", v_align="TOP")
        totals_row.cell("Sub-Total($):", colspan=1, align="L")
        totals_row.cell(_money(undiscounted_subtotal), align="R")

        row2 = table.row()
        row2.cell("Discount($):", align="L")
        row2.cell(_money(item_discount_total), align="R")

        row3 = table.row()
        row3.cell(special_discount_label, align="L")
        row3.cell(_money(order.discount_amount), align="R")

        bold = FontFace(emphasis="B")
        row4 = table.row()
        row4.cell("Grand Total:", style=bold, align="L")
        row4.cell(_money(order.grand_total), style=bold, align="R")

    # ---- payment QR, drawn into the terms cell the table just laid out ----
    # Bottom-anchored, because the only coordinate fpdf2 gives back for a table is where
    # the table ended - and the four totals rows this cell spans are always taller than
    # the QR block, so sitting it on the cell's bottom edge needs no row-height
    # arithmetic and stays correct if a row ever grows.
    if qr_image is not None:
        table_bottom = pdf.get_y()
        qr_x = pdf.l_margin + terms_width - _CELL_PADDING - _QR_SIZE_MM
        caption_top = table_bottom - _CELL_PADDING - _QR_CAPTION_MM
        pdf.image(qr_image, x=qr_x, y=caption_top - _QR_SIZE_MM, w=_QR_SIZE_MM, h=_QR_SIZE_MM)
        if qr_caption:
            # Right-aligned to the QR's own right edge, running left into the space the
            # wrapped terms text was kept out of - the account name is longer than the
            # picture is wide.
            _use_latin_font(pdf, 7)
            pdf.set_xy(qr_x - 20, caption_top)
            pdf.cell(_QR_SIZE_MM + 20, _QR_CAPTION_MM, qr_caption, align="R")
        # set_xy above moved the cursor into the middle of the table; put it back on the
        # table's bottom edge so the signature strip lands where it always did.
        pdf.set_y(table_bottom)

    # ---- signature strip, mirrors qpt-sign-strip ----
    # ~20mm below the table, matching .qpt-sign-strip's 76px margin-top on the
    # website's 794px-wide (= 210mm) template.
    pdf.ln(20)
    sign_y = pdf.get_y()
    sign_width = content_width / 5
    signatures = (
        ("ទទួលប្រាក់ដោយ", "Cash received by"),
        ("ទទួលដោយ", "Received by"),
        ("ដឹកដោយ", "Delivered by"),
        ("បញ្ជូនដោយ", "Issued by"),
        ("រៀបចំដោយ", "Prepared by"),
    )
    # .qpt-sign-line is tagged .qpt-khmer on the website too (both the Khmer word and
    # its English caption sit in the same tagged block), so the whole two-line caption
    # uses the Khmer font here, matching that.
    _use_khmer_font(pdf, 8)
    for i, (khmer, english) in enumerate(signatures):
        x = pdf.l_margin + i * sign_width
        pdf.line(x, sign_y, x + sign_width - 4, sign_y)
        pdf.set_xy(x, sign_y + 1.5)
        pdf.multi_cell(sign_width - 4, 3.6, f"{khmer}\n{english}", align="C")

    return bytes(pdf.output())
