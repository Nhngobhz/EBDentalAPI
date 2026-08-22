"""
Turns ABA's raw merchant transaction export (.xlsx) into the refined one-table PDF the
owner used to produce by hand - see the admin Reports screen's "ABA" card, which is the
only caller (POST /reports/aba, app/routers/reports.py).

What "refined" means here was taken from the hand-made examples: of ABA's 19 exported
columns only seven survive - Date/Time, Outlet, Payer, Original Amount, Original
Currency, Payment Amount, Payment Currency - the timestamp is trimmed to its date, and
the whole thing prints landscape with the header row repeating on every page.
Transaction ID / Seller / Accepted Via / Refunded By / Payment Type / Approval Code /
Transaction Type / Discount / Refund Remark / Channel / Processing Fees / Processing
Fees Settlement Date are all dropped. No rows are filtered out and their order is
preserved, so the PDF still reconciles line-for-line against the spreadsheet. A
spreadsheet somebody has already trimmed by hand prints as the narrower table it is
rather than being refused - see _COLUMNS.

Two things the hand-made version didn't have, added because a standalone PDF has to
identify itself: a title strip (outlet + period + row count) above the table, and a
per-currency totals strip below it. Nothing else about the table differs.

The workbook is read with the standard library (zipfile + ElementTree) rather than
openpyxl. This is one fixed, machine-generated export shape, and a new third-party
dependency would have to be carried into both the Docker image and the Windows-native
service install for it - see _read_workbook_rows for the small slice of the format that
actually gets parsed.

Fonts are the same two bundled faces the quotation PDF uses (app/assets/fonts/): Inter
throughout, with Noto Sans Khmer swapped in per-cell for payer names written in Khmer
script - ABA's Payer column carries plenty of them, and fpdf2 (unlike a browser) won't
fall back per-glyph on its own. See invoice_pdf.py, whose _has_khmer/FontFace approach
this mirrors, including why text shaping is only switched on when something needs it.
"""
import io
import re
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree

from fpdf import FPDF
from fpdf.enums import TableBordersLayout
from fpdf.fonts import FontFace

from app.core.logging_conf import get_logger

logger = get_logger("aba_report")

_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_LATIN_FONT = "Inter"
_KHMER_FONT = "NotoKhmer"

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_DOC_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# The columns that survive, in printed order, matched against the export's header row by
# name rather than by cell letter - ABA has changed its column set before, and a by-name
# lookup can't quietly print the wrong column under the right heading.
#
# This is the superset, not a requirement: whichever of these the upload actually has
# get printed, in this order. A raw ABA export has all seven; a spreadsheet somebody has
# already trimmed by hand comes out as the narrower table it is rather than being
# refused (only Date/Time plus two others are insisted on - see parse_aba_workbook).
_DATE = "Date/Time"
_OUTLET = "Outlet"
_PAYER = "Payer"
_COLUMNS = (
    _DATE,
    _OUTLET,
    _PAYER,
    "Original Amount",
    "Original Currency",
    "Payment Amount",
    "Payment Currency",
)
# Free-text columns a merchant can put Khmer script into. Everything else is a date, a
# currency code or a formatted number, so only these two are ever font-switched.
_KHMER_CAPABLE = (_OUTLET, _PAYER)

# Body text one notch up from the 7.5pt this started at, on the owner's request
# (2026-08-22). The heading row stays half a point smaller: at the body size "Payment
# Amount" no longer fits on one line, and it wrapping while "Original Amount" didn't
# left the header row visibly lopsided.
_BODY_PT = 8.5
_HEADING_PT = 8

# Share of the printable width and text alignment per column. The shares are normalised
# over whichever columns are actually present, so a trimmed export still fills the page.
# Payer gets the most: it holds by far the longest values
# ("SENG CHANTHIDA AND TANN SREYPOR (*408)").
_COL_LAYOUT = {
    _DATE: (0.10, "CENTER"),
    _OUTLET: (0.22, "LEFT"),
    _PAYER: (0.32, "LEFT"),
    "Original Amount": (0.10, "CENTER"),
    "Original Currency": (0.075, "CENTER"),
    "Payment Amount": (0.10, "CENTER"),
    "Payment Currency": (0.085, "CENTER"),
}

# Excel's serial-date epoch. 1899-12-30, not 12-31, absorbs the deliberate
# 1900-is-a-leap-year bug Excel keeps for Lotus compatibility.
_EXCEL_EPOCH = datetime(1899, 12, 30)


class AbaReportError(ValueError):
    """The upload isn't a usable ABA merchant transaction export. Carries a message
    meant to be read by whoever picked the file, so the router can hand it straight back
    as a 400 instead of letting a parse error surface as a 500."""


# ---------------------------------------------------------------------------
# Reading the workbook
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _column_index(cell_ref: str) -> int:
    """"C" / "AB12" -> 0-based column number. Cells holding nothing are simply absent
    from the XML (the export omits Discount on most rows), so a row has to be assembled
    by reference rather than by counting the <c> elements it happens to contain."""
    letters = re.match(r"[A-Z]+", cell_ref or "")
    if not letters:
        return -1
    index = 0
    for char in letters.group(0):
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []  # a workbook written entirely with inline strings has no such part
    strings = []
    for si in ElementTree.fromstring(raw):
        # One <si> can be split across several <t> runs (formatting changing mid-cell),
        # so its text is every run concatenated.
        strings.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
    return strings


def _first_worksheet_name(archive: zipfile.ZipFile) -> str:
    """The path of the sheet that opens first, resolved workbook.xml -> its rels. Taking
    "xl/worksheets/sheet1.xml" on faith would be wrong for any workbook whose first tab
    isn't the first file in the archive."""
    try:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except KeyError as exc:
        raise AbaReportError("That file isn't a readable Excel workbook.") from exc

    sheet = workbook.find(f"{_NS}sheets/{_NS}sheet")
    if sheet is None:
        raise AbaReportError("That workbook has no sheets.")
    rel_id = sheet.get(f"{_DOC_REL_NS}id")

    for rel in rels:
        if rel.get("Id") == rel_id:
            target = rel.get("Target", "")
            return target[1:] if target.startswith("/") else f"xl/{target.lstrip('./')}"
    return "xl/worksheets/sheet1.xml"


def _date_styles(archive: zipfile.ZipFile) -> set[int]:
    """Style indexes whose number format renders as a date/time.

    ABA writes Date/Time as text ("2026-08-20 08:41:41"), so this is only a safety net
    for a workbook that has been re-saved with the column converted to a real datetime -
    such a cell holds a bare serial number and would otherwise print as "46264.36".
    Built-in numFmtIds 14-22 and 45-47 are the date/time ones; custom formats are spotted
    by looking for date tokens once quoted literals and escapes are stripped out."""
    try:
        styles = ElementTree.fromstring(archive.read("xl/styles.xml"))
    except KeyError:
        return set()

    date_fmt_ids = {14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47}
    for fmt in styles.iter(f"{_NS}numFmt"):
        code = re.sub(r'"[^"]*"|\\.', "", fmt.get("formatCode", ""))
        if re.search(r"[yYdDhHsS]|m{3,}", code):
            try:
                date_fmt_ids.add(int(fmt.get("numFmtId", "-1")))
            except ValueError:
                pass

    cell_xfs = styles.find(f"{_NS}cellXfs")
    if cell_xfs is None:
        return set()
    return {
        index
        for index, xf in enumerate(cell_xfs)
        if int(xf.get("numFmtId", "0") or 0) in date_fmt_ids
    }


def _cell_text(cell, shared: list[str], date_styles: set[int]) -> str:
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{_NS}t")).strip()

    value = cell.find(f"{_NS}v")
    raw = (value.text or "").strip() if value is not None else ""
    if not raw:
        return ""
    if cell_type == "s":
        try:
            index = int(raw)
        except ValueError:
            return ""
        return shared[index].strip() if 0 <= index < len(shared) else ""
    if cell_type == "e":
        return ""  # #N/A and friends print as blank, not as the error literal
    if cell_type is None and cell.get("s"):
        try:
            styled = int(cell.get("s")) in date_styles
        except ValueError:
            styled = False
        if styled:
            try:
                return (_EXCEL_EPOCH + timedelta(days=float(raw))).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OverflowError):
                return raw
    return raw


def _read_workbook_rows(data: bytes) -> list[list[str]]:
    """Every row of the first sheet as a list of strings, header row included."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise AbaReportError(
            "That file isn't an .xlsx workbook. Upload the Excel file ABA gives you, "
            "not a PDF or a CSV."
        ) from exc

    with archive:
        shared = _shared_strings(archive)
        date_styles = _date_styles(archive)
        try:
            sheet_xml = archive.read(_first_worksheet_name(archive))
        except KeyError as exc:
            raise AbaReportError("That workbook's first sheet couldn't be read.") from exc

        rows: list[list[str]] = []
        # iterparse over the sheet rather than one big tree: a whole month's export runs
        # to thousands of rows, and only one row is ever needed at a time.
        for _, element in ElementTree.iterparse(io.BytesIO(sheet_xml), events=("end",)):
            if _local(element.tag) != "row":
                continue
            cells: dict[int, str] = {}
            for cell in element.findall(f"{_NS}c"):
                index = _column_index(cell.get("r", ""))
                if index >= 0:
                    cells[index] = _cell_text(cell, shared, date_styles)
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
            element.clear()
    return rows


# ---------------------------------------------------------------------------
# Shaping what gets printed
# ---------------------------------------------------------------------------

def _money(text: str) -> str:
    """"8.5" -> "8.50", "64880" -> "64,880.00". Grouped to two decimals whatever the
    currency, matching the hand-made PDFs (KHR is printed "247,355.00" there too).
    Anything unparseable is passed straight through rather than blanked, so a figure is
    never silently lost off the report."""
    cleaned = (text or "").replace(",", "").strip()
    if not cleaned:
        return ""
    try:
        return f"{Decimal(cleaned):,.2f}"
    except InvalidOperation:
        return text.strip()


def _amount_value(text: str) -> Decimal | None:
    cleaned = (text or "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _day(text: str) -> str:
    """The date half of "2026-08-20 08:41:41". The time is deliberately dropped - it's
    what the hand-made PDFs show, and a day's export is one day anyway."""
    return (text or "").strip().split(" ", 1)[0].split("T", 1)[0]


def _has_khmer(text: str) -> bool:
    return any("ក" <= ch <= "៿" for ch in text or "")


def _parse_day(text: str) -> date | None:
    try:
        return datetime.strptime(_day(text), "%Y-%m-%d").date()
    except ValueError:
        return None


class AbaReport:
    """The refined table, ready to print: `columns` are the kept column names in printed
    order and `rows` their values, alongside the few figures the title and totals strips
    need."""

    def __init__(self, columns, rows, outlets, totals, first_day, last_day):
        self.columns = columns
        self.rows = rows
        self.outlets = outlets
        self.totals = totals  # {("Original"|"Payment", currency): Decimal}
        self.first_day = first_day
        self.last_day = last_day

    @property
    def period(self) -> str:
        if not self.first_day:
            return ""
        if self.last_day and self.last_day != self.first_day:
            return f"{self.first_day:%d %b %Y} - {self.last_day:%d %b %Y}"
        return f"{self.first_day:%d %b %Y}"


def parse_aba_workbook(data: bytes) -> AbaReport:
    rows = _read_workbook_rows(data)
    if not rows:
        raise AbaReportError("That workbook is empty.")

    # The header isn't assumed to be row 1: should ABA ever prefix the export with a
    # title band, the columns are still found. Only the first few rows are searched, so
    # a stray value further down a data column can't be mistaken for a header.
    header_index, header, columns = None, [], []
    for i, row in enumerate(rows[:20]):
        candidate = [cell.strip() for cell in row]
        present = [name for name in _COLUMNS if name in candidate]
        if _DATE in present and len(present) >= 3:
            header_index, header, columns = i, candidate, present
            break

    if header_index is None:
        raise AbaReportError(
            "That doesn't look like an ABA merchant transaction report - no header row "
            "with 'Date/Time' and at least two of "
            + ", ".join(f"'{name}'" for name in _COLUMNS if name != _DATE)
            + " was found."
        )

    positions = [header.index(name) for name in columns]
    date_at = columns.index(_DATE)
    # Both halves of a money pair have to be present for a total to mean anything - an
    # amount with no currency beside it can't be added to either running figure.
    total_pairs = [
        (label, columns.index(f"{label} Amount"), columns.index(f"{label} Currency"))
        for label in ("Original", "Payment")
        if f"{label} Amount" in columns and f"{label} Currency" in columns
    ]
    amount_at = [i for i, name in enumerate(columns) if name.endswith(" Amount")]

    kept: list[list[str]] = []
    days: list[date] = []
    totals: dict[tuple[str, str], Decimal] = {}
    for row in rows[header_index + 1:]:
        values = [row[p] if p < len(row) else "" for p in positions]
        if not any(value.strip() for value in values):
            continue  # the trailing blank rows Excel leaves behind

        # Totals are summed per currency, not per column: a KHQR payment can settle in
        # KHR against a USD original, so one grand total would be adding riels to
        # dollars.
        for label, amount_index, currency_index in total_pairs:
            amount = _amount_value(values[amount_index])
            currency = values[currency_index].strip()
            if amount is not None and currency:
                totals[(label, currency)] = totals.get((label, currency), Decimal(0)) + amount

        day = _parse_day(values[date_at])
        if day:
            days.append(day)

        values[date_at] = _day(values[date_at])
        for i in amount_at:
            values[i] = _money(values[i])
        kept.append(values)

    if not kept:
        raise AbaReportError("That export has no transactions in it.")

    outlets = []
    if _OUTLET in columns:
        outlet_at = columns.index(_OUTLET)
        outlets = sorted({row[outlet_at].strip() for row in kept if row[outlet_at].strip()})

    return AbaReport(
        columns=columns,
        rows=kept,
        outlets=outlets,
        totals=totals,
        first_day=min(days) if days else None,
        last_day=max(days) if days else None,
    )


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

class _AbaPDF(FPDF):
    """Page number in the footer; no running header - the table's own heading row is
    what repeats, and fpdf2 redraws that itself on every page break."""

    def header(self):
        pass

    def footer(self):
        self.set_y(-10)
        self.set_text_shaping(False)
        self.set_font(_LATIN_FONT, "", 7.5)
        self.set_text_color(130, 130, 130)
        self.cell(0, 5, f"Page {self.page_no()} of {{nb}}", align="C")
        self.set_text_color(0, 0, 0)


def build_aba_report_pdf(report: AbaReport) -> bytes:
    # Landscape Letter: the same page the hand-made PDFs print on (those are portrait
    # Letter carrying /Rotate 90, which comes out identical).
    pdf = _AbaPDF(unit="mm", format="Letter", orientation="L")
    pdf.set_margins(8, 8, 8)
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_font(_KHMER_FONT, "", str(_FONTS_DIR / "NotoSansKhmer-Regular.ttf"))
    pdf.add_font(_KHMER_FONT, "B", str(_FONTS_DIR / "NotoSansKhmer-Bold.ttf"))
    pdf.add_font(_LATIN_FONT, "", str(_FONTS_DIR / "Inter-Regular.ttf"))
    pdf.add_font(_LATIN_FONT, "B", str(_FONTS_DIR / "Inter-Bold.ttf"))
    pdf.alias_nb_pages()
    pdf.add_page()

    content_width = pdf.w - pdf.l_margin - pdf.r_margin

    # ---- title strip (page 1 only) ----
    pdf.set_text_shaping(False)
    pdf.set_font(_LATIN_FONT, "B", 13)
    pdf.cell(content_width, 6, "ABA Merchant Transaction Report",
             align="L", new_x="LMARGIN", new_y="NEXT")

    subtitle = "  |  ".join(
        part
        for part in (
            ", ".join(report.outlets),
            report.period,
            f"{len(report.rows)} transaction{'' if len(report.rows) == 1 else 's'}",
        )
        if part
    )
    pdf.set_font(_LATIN_FONT, "", 8.5)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(content_width, 4.5, subtitle, align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2.5)

    # ---- the table ----
    # Widths are normalised over the columns actually present, so an already-trimmed
    # export still spans the full page instead of leaving a gap where Payer would be.
    shares = [_COL_LAYOUT[name][0] for name in report.columns]
    scale = content_width / sum(shares)
    col_widths = tuple(share * scale for share in shares)
    text_align = tuple(_COL_LAYOUT[name][1] for name in report.columns)
    khmer_at = {i for i, name in enumerate(report.columns) if name in _KHMER_CAPABLE}

    khmer_style = FontFace(family=_KHMER_FONT)
    # Shaping is a document-level switch, so it only goes on when a name actually needs
    # it - see the ToUnicode note in invoice_pdf.py's docstring.
    if any(_has_khmer(row[i]) for row in report.rows for i in khmer_at):
        pdf.set_text_shaping(True)

    pdf.set_font(_LATIN_FONT, "", _BODY_PT)
    with pdf.table(
        col_widths=col_widths,
        first_row_as_headings=True,
        num_heading_rows=1,
        text_align=text_align,
        borders_layout=TableBordersLayout.ALL,
        headings_style=FontFace(
            family=_LATIN_FONT, emphasis="B", size_pt=_HEADING_PT, fill_color=(238, 241, 245)
        ),
        line_height=5,
        padding=1.1,
    ) as table:
        head = table.row()
        for name in report.columns:
            head.cell(name)

        for values in report.rows:
            row = table.row()
            for index, text in enumerate(values):
                needs_khmer = index in khmer_at and _has_khmer(text)
                row.cell(text, style=khmer_style if needs_khmer else None)

    # ---- totals strip ----
    pdf.set_text_shaping(False)
    if report.totals:
        pdf.ln(3)
        pdf.set_font(_LATIN_FONT, "B", 9)
        pdf.cell(content_width, 5, "Totals", align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(_LATIN_FONT, "", 9)
        for label in ("Original", "Payment"):
            parts = [
                f"{amount:,.2f} {currency}"
                for (kind, currency), amount in sorted(report.totals.items())
                if kind == label
            ]
            if parts:
                pdf.cell(
                    content_width, 5, f"{label} Amount:   " + "     ".join(parts),
                    align="L", new_x="LMARGIN", new_y="NEXT",
                )

    return bytes(pdf.output())


def refined_filename(source_filename: str | None) -> str:
    """The PDF's download name, derived from the uploaded workbook's: the extension
    changes and the browser's " (2)" duplicate-download suffix comes off, so
    "..._472 (2).xlsx" lands as "..._472.pdf" - exactly the names the hand-made files
    already carry, so a re-run overwrites the old copy instead of piling up beside it.
    Any directory part of the name is discarded (Path().stem), which also keeps a
    hostile filename from steering the Content-Disposition header."""
    stem = Path((source_filename or "").replace("\\", "/")).stem.strip()
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    stem = re.sub(r'[\r\n"]', "", stem)
    return f"{stem or 'ABA_Merchant_transaction_report'}.pdf"


def refine_aba_report(data: bytes, source_filename: str | None = None) -> tuple[bytes, str]:
    """(pdf_bytes, download_filename). Raises AbaReportError for anything that isn't an
    ABA export the seven columns can be read out of."""
    report = parse_aba_workbook(data)
    logger.info(
        "Refined ABA report: %d rows, %s",
        len(report.rows),
        report.period or "no dated rows",
    )
    return build_aba_report_pdf(report), refined_filename(source_filename)
