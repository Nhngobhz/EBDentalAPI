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
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import CellBordersLayout, TableBordersLayout
from fpdf.fonts import FontFace

from app.schemas import OrderOut

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


# Same reconstruction as EB Web Project/formatting.py's derive_old_price() and
# main.js's deriveOldUnitPrice() - store-api only ever stores the final charged
# unit_price + the discount that produced it, never a separate original-price column.
def _derive_old_unit_price(unit_price: Decimal, discount: Decimal, discount_type: str) -> Decimal:
    price = Decimal(unit_price)
    d = Decimal(discount or 0)
    if not d:
        return price
    if discount_type == "cash":
        return price + d
    if d >= 100:
        return price
    return price / (Decimal(1) - d / Decimal(100))


def _format_plain_number(value: Decimal) -> str:
    """Decimal.normalize() can flip a round number like 10 into scientific notation
    ("1E+1") - format as a fixed-point string and trim trailing zeros instead, matching
    what main.js's `Number(discount) + '%'` prints (e.g. "10%", "12.5%")."""
    s = f"{Decimal(value):f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _format_item_discount(discount: Decimal, discount_type: str) -> str:
    if not discount:
        return "—"  # em dash, matches the website's "—" placeholder
    return f"$ {Decimal(discount):.2f}" if discount_type == "cash" else f"{_format_plain_number(discount)}%"


def _money(value) -> str:
    return f"$ {Decimal(value):.2f}"


class _QuotePDF(FPDF):
    def header(self):
        pass

    def footer(self):
        pass


def build_invoice_pdf(order: OrderOut) -> bytes:
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

    # ---- header: brand (left) / "Quotation" + No/Date (right) ----
    # Font sizes/positions mirror qpt-brand-name/qpt-title (1.6-1.7rem) and
    # qpt-brand-meta/qpt-meta-right (0.72-0.75rem) at ~96dpi/16px-root.
    pdf.set_xy(pdf.l_margin, top)
    _use_latin_font(pdf, 20, bold=True)
    pdf.cell(content_width / 2, 9, "EB DENTAL")
    pdf.set_xy(pdf.l_margin + content_width / 2, top)
    pdf.cell(content_width / 2, 9, "Quotation", align="R")

    pdf.set_xy(pdf.l_margin, top + 9)
    _use_latin_font(pdf, 8.5)
    pdf.multi_cell(content_width / 2, 4.2, "Phnom Penh, Cambodia\nTel: 012 81 89 58 / 011 81 89 58")

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
        y = info_top
        for label, value in rows:
            pdf.set_xy(x, y)
            _use_latin_font(pdf, 9)
            pdf.cell(label_width, 5, label)
            if label in khmer_fields:
                _use_khmer_font(pdf, 9, bold=True)
            else:
                _use_latin_font(pdf, 9, bold=True)
            pdf.cell(col_width - label_width, 5, str(value or "—"))
            y += 5
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
        ("Payment Term", order.payment_term or "COD"),
        ("Salesperson", order.salesperson),
        ("User", order.quoted_by_name),
        ("Installation Term", order.install_term or "Free within Phnom Penh"),
        ("Contact Person", order.contact_person),
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

    khmer_style = FontFace(family=_KHMER_FONT)
    undiscounted_subtotal = Decimal("0")
    item_rows = []
    for i, item in enumerate(order.items, start=1):
        old_unit_price = _derive_old_unit_price(item.unit_price, item.discount, item.discount_type)
        undiscounted_subtotal += old_unit_price * item.qty
        code = item.product_code or "—"
        # Product code/name are plain free text - unlike Clinic/Address, the website
        # doesn't tag this column .qpt-khmer either, but a browser still auto-falls-back
        # per-glyph to a font that has it, which fpdf2 won't do on its own - so any cell
        # that actually contains Khmer script gets the Khmer font+shaping explicitly.
        item_rows.append((
            (str(i), None), (code, khmer_style if _has_khmer(code) else None),
            (item.product_name, khmer_style if _has_khmer(item.product_name) else None),
            (str(item.qty), None), (item.uom or "PCS", None), (_money(old_unit_price), None),
            (_format_item_discount(item.discount, item.discount_type), None), (_money(item.line_amount), None),
        ))

    item_discount_total = max(Decimal("0"), undiscounted_subtotal - Decimal(order.subtotal))
    special_discount_label = (
        "Special Discount (Cash):" if order.discount_type == "cash"
        else f"Special Discount ({_format_plain_number(order.discount_value)}%):"
    )

    # Pads the table with blank rows so it always looks like a full, pre-printed form
    # (like the paper original) even when there are only a few items - mirrors
    # MIN_TABLE_ROWS in main.js's buildPrintTemplate.
    MIN_TABLE_ROWS = 22
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
        num_heading_rows=2,
        text_align=("CENTER", "LEFT", "LEFT", "CENTER", "CENTER", "RIGHT", "CENTER", "RIGHT"),
        borders_layout=TableBordersLayout.ALL,
        line_height=5,
        padding=1.2,
    ) as table:
        # head1's "UP before & After Discount" cell and head2's two filler cells (needed
        # to fill out the grid under a colspan header in a rowspan=2 layout) skip the
        # border between them, so the two don't render as a separate boxed sliver row -
        # just one seamless header cell with a divider that only kicks in at the data
        # rows below.
        no_bottom = CellBordersLayout.LEFT | CellBordersLayout.RIGHT | CellBordersLayout.TOP
        no_top = CellBordersLayout.LEFT | CellBordersLayout.RIGHT | CellBordersLayout.BOTTOM
        head1 = table.row()
        head1.cell("No.", rowspan=2)
        head1.cell("Code", rowspan=2)
        head1.cell("Description", rowspan=2)
        head1.cell("Qty", rowspan=2)
        head1.cell("UOM", rowspan=2)
        head1.cell("UP before & After Discount", colspan=2, border=no_bottom)
        head1.cell("Amount", rowspan=2)
        head2 = table.row()
        head2.cell("", border=no_top)
        head2.cell("", border=no_top)

        for cells in item_rows:
            row = table.row()
            for text, style in cells:
                row.cell(text, style=style)

        for _ in range(blank_rows_needed):
            row = table.row()
            for _ in col_widths:
                row.cell("")

        totals_row = table.row()
        totals_row.cell(
            "Quotation valid for 30 days from the date issued.",
            colspan=6, rowspan=4, align="L", v_align="TOP",
        )
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

    # ---- signature strip, mirrors qpt-sign-strip ----
    pdf.ln(14)
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
