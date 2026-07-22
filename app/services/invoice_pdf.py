"""
Server-side invoice PDF, used only as the Telegram order-alert attachment (see
send_order_alert() in app/services/telegram.py). This is NOT the official quotation PDF
the customer receives - that one is built entirely client-side in the EB Web Project's
main.js (QuoteCart.buildPrintTemplate/exportPDF) from html2canvas, which is what lets it
render Khmer glyphs. This one only needs to be readable by staff inside Telegram, so it
sticks to fpdf2's built-in Helvetica font (no Khmer glyph support) and a plain layout.
"""
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from fpdf import FPDF

from app.schemas import OrderOut


def _money(value: Decimal | float) -> str:
    return f"$ {Decimal(value):.2f}"


def build_invoice_pdf(order: OrderOut) -> bytes:
    pdf = FPDF(unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "EB DENTAL - Invoice", ln=1)

    pdf.set_font("Helvetica", "", 10)
    created = order.created_at or datetime.utcnow()
    pdf.cell(0, 6, f"Order No: {order.order_number}    Quote Code: {order.quote_code}", ln=1)
    pdf.cell(0, 6, f"Date: {created.strftime('%d/%m/%Y')}    Status: {order.status}", ln=1)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Clinic Information", ln=1)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Clinic: {order.clinic_name}", ln=1)
    if order.contact_person:
        pdf.cell(0, 6, f"Contact: {order.contact_person}", ln=1)
    pdf.cell(0, 6, f"Phone: {order.phone}", ln=1)
    pdf.cell(0, 6, f"Address: {order.address}", ln=1)
    pdf.cell(0, 6, f"Salesperson: {order.salesperson or '-'}    Placed by: {order.quoted_by_name or '-'}", ln=1)
    pdf.ln(4)

    col_widths = (10, 70, 15, 25, 25, 25)
    headers = ("No", "Item", "Qty", "Unit $", "Line $", "Discount")
    pdf.set_font("Helvetica", "B", 9)
    for width, header in zip(col_widths, headers):
        pdf.cell(width, 7, header, border=1)
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    for i, item in enumerate(order.items, start=1):
        discount_label = (
            f"{item.discount}%" if item.discount_type == "percent" and item.discount else
            _money(item.discount) if item.discount else "-"
        )
        row = (
            str(i),
            item.product_name[:40],
            str(item.qty),
            _money(item.unit_price),
            _money(item.line_amount),
            discount_label,
        )
        for width, value in zip(col_widths, row):
            pdf.cell(width, 6, value, border=1)
        pdf.ln()

    pdf.ln(4)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Sub-Total: {_money(order.subtotal)}", ln=1, align="R")
    discount_label = "Cash" if order.discount_type == "cash" else f"{order.discount_value}%"
    pdf.cell(0, 6, f"Discount ({discount_label}): {_money(order.discount_amount)}", ln=1, align="R")
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Grand Total: {_money(order.grand_total)}", ln=1, align="R")

    buffer = BytesIO(pdf.output())
    return buffer.getvalue()
