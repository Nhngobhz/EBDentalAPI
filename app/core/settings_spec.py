"""
The catalogue of site-wide settings an admin can change from the Settings screen.

This module is the single source of truth for what a setting *is*: its type, its
default, which group it belongs to and how it's labelled. The API validates against it,
the Flask admin page renders its form from it, and `app/services/app_settings.py` merges
it with the override rows in `app_settings`. Adding a setting means adding a `Setting(...)`
below and reading it where it should take effect - no migration, no schema change.

Two rules keep this honest:

1. **Every setting here is actually read by something.** A settings page full of knobs
   that do nothing is worse than no settings page, because the next person can't tell
   which half works. Each group's docstring names its consumers.
2. **Nothing secret lives here.** Credentials (PayWay, Bakong, Telegram, SMTP, R2,
   Google) stay in the environment - see app/config.py. The Settings screen reports
   whether each is configured (`app/routers/settings.py::_integration_status`) but never
   stores or echoes the values.

`public=True` marks a setting the storefront needs before anyone has signed in - it is
served by the unauthenticated `GET /settings/public`, so never mark anything internal
public. Anything the printed quote/invoice shows counts as public: those documents are
built in the customer's browser.
"""
from dataclasses import dataclass, field
from typing import Any

from app.config import settings as env


@dataclass(frozen=True)
class Setting:
    key: str
    group: str
    label: str
    # image: the value is a stored picture URL/path (exactly like every *_image field
    # in the schema - see app/core/files.py), written by POST /settings/image/{key}
    # rather than typed. Anything that renders the form has to offer a file input for
    # it, not a text box.
    type: str  # text | textarea | url | email | number | bool | image
    default: Any
    help: str = ""
    public: bool = True
    # Numbers only. Enforced by coerce() below, which is what the API validates with.
    minimum: float | None = None
    maximum: float | None = None
    # Textareas render this many rows in the admin form.
    rows: int = 3


@dataclass(frozen=True)
class Group:
    id: str
    label: str
    icon: str  # Font Awesome class, matching the rest of the admin panel
    blurb: str
    settings: tuple = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Group 1: Store & contact details
#
# Consumers: templates/partials/footer.html (every storefront page) and
# templates/contact.html. Before this existed, changing the shop's phone number
# meant editing two templates.
# ---------------------------------------------------------------------------
_STORE = (
    Setting("store_name", "store", "Store name", "text", "EB Dental Supply",
            "Used in the footer and as the fallback name across the site."),
    Setting("store_tagline", "store", "Tagline", "text", "Partner in Dentistry Since 1985",
            "The short line under the footer logo."),
    Setting("store_blurb", "store", "Footer description", "textarea",
            "With over 40 years of experience, EB Dental Supply has been a trusted partner "
            "for dental professionals across Cambodia. We provide high-quality instruments, "
            "equipment, and consumables from the world's leading brands.",
            "The paragraph in the footer's first column.", rows=4),
    Setting("years_badge", "store", "Experience badge", "text", "40+ Years of Excellence",
            "Small badge shown in the footer. Leave empty to hide it."),
    Setting("contact_phone", "store", "Phone", "text", "098 882 953",
            "Shown in the footer and on the contact page."),
    Setting("contact_email", "store", "Email", "email", "info@ebdental.com"),
    Setting("contact_address", "store", "Address", "textarea",
            "Orussey Market, Phnom Penh, Cambodia",
            "Shown on the contact page's location card.", rows=2),
    Setting("business_hours", "store", "Opening hours", "text", "Mon–Sat, 8am – 6pm"),
    Setting("map_embed_url", "store", "Google Maps embed URL", "url",
            "https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d2324.195545186654"
            "!2d104.91529819224563!3d11.56508728838983!2m3!1f0!2f0!3f0!3m2!1i1024!2i768"
            "!4f13.1!3m3!1m2!1s0x3109513fe8b9a13d%3A0xda6f77bbf34bab03"
            "!2sPharmacie%20Eng%20Bong!5e0!3m2!1sen!2skh!4v1786157476273!5m2!1sen!2skh",
            "The src of the iframe from Google Maps → Share → Embed a map. "
            "Leave empty to hide the map."),
    Setting("call_now_url", "store", "\"Call Now\" button link", "url",
            "https://t.me/engbongmachine",
            "Where the contact page's Call Now button goes - a tel: link or a chat link."),
    Setting("facebook_machinery_url", "store", "Facebook — Machinery", "url",
            "https://www.facebook.com/profile.php?id=61586127287882",
            "Leave empty to hide this card on the contact page."),
    Setting("facebook_materials_url", "store", "Facebook — Materials", "url",
            "https://www.facebook.com/profile.php?id=61581818935154"),
    Setting("facebook_slock_url", "store", "Facebook — Slock Implant", "url",
            "https://www.facebook.com/profile.php?id=61585714587756"),
    Setting("footer_copyright", "store", "Copyright line", "text",
            "© 2026 EB Dental Supply. All rights reserved."),
)

# ---------------------------------------------------------------------------
# Group: About page
#
# Consumer: templates/main/about.html.
#
# These exist because the About page was repeating facts the Store group already
# owned - it hardcoded "since 1985" next to an editable tagline saying the same
# thing, and "Orussey Market" next to an editable address. The prose itself is a
# setting rather than the individual facts spliced into a fixed sentence: the shop
# should be able to rewrite its own story, not just change the year inside ours.
# ---------------------------------------------------------------------------
_ABOUT = (
    Setting("about_intro", "about", "Hero paragraph", "textarea",
            "EB Dental Supply equips clinics, laboratories and dental professionals "
            "across the country with instruments, equipment and consumables from the "
            "world's leading manufacturers.",
            "The lead paragraph under the About page's headline.", rows=3),
    Setting("stat_years", "about", "Stat 1 — Years of Excellence", "text", "40+",
            "The three big numbers across the About page. Leave one empty to hide it."),
    Setting("stat_brands", "about", "Stat 2 — Trusted Brands", "text", "100+"),
    Setting("stat_clinicians", "about", "Stat 3 — Happy Clinicians", "text", "5000+"),
    Setting("about_story_heading", "about", "Our Story heading", "text",
            "A name dental professionals have trusted since 1985"),
    Setting("about_story", "about", "Our Story", "textarea",
            "For over 40 years, EB Dental Supply has been a trusted partner for dental "
            "professionals across Cambodia and the region. We provide high-quality "
            "instruments, equipment, and consumables from the world's leading brands, "
            "ensuring that practitioners have access to the best tools for patient care.\n"
            "From our home at Orussey Market in Phnom Penh, we support everything from a "
            "single hand instrument to a full clinic fit-out — backed by a team that knows "
            "the products it sells and stands behind them long after delivery.",
            "One paragraph per line.", rows=7),
)

# The "qr" group (the four contact-page QR captions) used to live here. It moved to a
# real table - see app/models.py::QrCode and app/routers/qr_codes.py - because a
# key/value spec can only describe a fixed number of cards, and the whole point of the
# new screen is that staff can add a fifth department and swap the pictures themselves.
# Migration d3b7f1c5a92e copies any values saved here into that table; the old override
# rows are left in app_settings, harmlessly ignored (see services/app_settings.py).

# ---------------------------------------------------------------------------
# Group 2: Printed quote / invoice / receipt
#
# Consumers: QuoteCart.buildPrintTemplate() in EB Web Project/static/js/main.js
# (the printed HTML + the client-side jsPDF export) AND
# app/services/invoice_pdf.py (the server-side PDF relayed to Telegram).
#
# Those two are separate engines rendering the same document and MUST agree -
# see the eb-quote-parity skill. Every setting below is read by both.
# ---------------------------------------------------------------------------
_DOCUMENT = (
    Setting("document_brand_name", "document", "Letterhead name", "text", "EB DENTAL",
            "Large name in the top-left of the printed quote/invoice."),
    Setting("document_address_line", "document", "Letterhead address", "text",
            "Phnom Penh, Cambodia"),
    Setting("document_tel_line", "document", "Letterhead phone", "text",
            "012 81 89 58 / 011 81 89 58",
            "Printed as \"Tel: <value>\" under the letterhead name."),
    Setting("quote_validity_days", "document", "Quote validity (days)", "number", 30,
            "How long a quotation says it stays valid for.", minimum=1, maximum=365),
    # The rest of the terms box printed at the foot of a QUOTATION, under the validity
    # line: two more lines of standing wording, and the bank QR with the account name
    # under it. Quotation only - an invoice is already paid and a cancelled order is not
    # payable, so neither prints a "scan to pay" QR (see buildPrintTemplate in main.js
    # and build_invoice_pdf here; both drop the whole block).
    Setting("quote_deposit_note", "document", "Deposit line", "text",
            "After receiving the deposit, Seller shall issue Proforma Invoice.",
            "Printed under the validity line. Leave empty to drop the line."),
    Setting("quote_payment_note", "document", "Payment line", "text",
            "ABA Bank Account: Please scan QR code for payment",
            "Printed under the deposit line, next to the QR. Leave empty to drop it."),
    Setting("quote_payment_qr", "document", "Payment QR picture", "image", "",
            "The bank QR printed at the right of that box. Upload the picture your "
            "banking app produced - it is stored exactly as uploaded (never "
            "re-compressed), because JPEG ringing around a QR's hard edges is what "
            "makes a small printed code fail to scan."),
    Setting("quote_payment_qr_caption", "document", "Payment QR caption", "text",
            "Bong Sucheng Home 49",
            "The account name printed under the QR."),
    # Keys still say "receipt" because the printed document was called one until
    # 2026-08-17; only the word on the page (and these labels) changed, and renaming a
    # settings key would strand whatever the admin had typed under the old one.
    Setting("receipt_note_khqr", "document", "Invoice note — paid by KHQR", "text",
            "Paid via KHQR. Thank you for your purchase.",
            "Replaces the validity line once an order is paid."),
    Setting("receipt_note_cash", "document", "Invoice note — paid in cash", "text",
            "Paid in full. Thank you for your purchase."),
    # These two were the same two string literals in two repos: constants in the Flask
    # app's blueprints/quote.py and, separately, the `or ...` fallbacks in
    # services/invoice_pdf.py here. They print on every quote, so a change to one and
    # not the other is exactly the drift this group exists to prevent.
    Setting("default_payment_term", "document", "Default payment term", "text", "COD",
            "Applied to every customer order and shown in their cart. Staff still type "
            "their own per quote - they are negotiating them."),
    Setting("default_install_term", "document", "Default installation term", "text",
            "Free within Phnom Penh"),
    # EB's own contact - it prints in the right-hand column of the quotation, beside
    # Salesperson and User, not in the customer's column. So it is ours to state, the
    # same way the two terms above are: a customer's cart shows it read-only and
    # blueprints/quote.py substitutes it onto their order rather than trusting the
    # request. Staff still type a per-quote one when a deal has its own contact.
    Setting("default_contact_person", "document", "Default contact person", "text",
            "098 882 953",
            "Printed as “Contact Person” on every customer order, and shown "
            "read-only in their cart. Staff can still type their own per quote."),
)

# ---------------------------------------------------------------------------
# Group: KHQR payments
#
# Consumers: app/services/khqr.py (tags 59/60 and the tag-99 expiry it writes into
# every generated QR) and app/routers/orders.py (the PendingCheckout deadline, which
# has to match the deadline inside the QR).
#
# NOT credentials, which is why these three can live here while BAKONG_ACCOUNT_ID,
# BAKONG_API_TOKEN and the PayWay keys stay in the environment. Each default is the
# env-resolved value from app/config.py rather than a literal, so a deployment that
# already sets KHQR_MERCHANT_NAME in .env keeps it and an override here layers on top.
#
# public=False: nothing in a browser needs these, and the merchant name reaches the
# payer through the QR payload itself.
# ---------------------------------------------------------------------------
_PAYMENT = (
    Setting("khqr_merchant_name", "payment", "Merchant name", "text",
            env.KHQR_MERCHANT_NAME,
            "Shown by the payer's banking app. Truncated to 25 characters by the "
            "KHQR spec.", public=False),
    Setting("khqr_merchant_city", "payment", "Merchant city", "text",
            env.KHQR_MERCHANT_CITY, "Truncated to 15 characters by the KHQR spec.",
            public=False),
    Setting("khqr_expiry_minutes", "payment", "QR valid for (minutes)", "number",
            env.KHQR_EXPIRY_MINUTES,
            "How long a generated QR stays payable. Staff can always issue a fresh one "
            "against the same order.", public=False, minimum=1, maximum=1440),
)

# ---------------------------------------------------------------------------
# Group 3: Maintenance
#
# Consumer: the _maintenance_gate before_request in EB Web Project/app.py.
# Signed-in staff always pass through, so turning this on can't lock the admin
# out of the panel that turns it off again.
# ---------------------------------------------------------------------------
_MAINTENANCE = (
    Setting("maintenance_mode", "maintenance", "Maintenance mode", "bool", False,
            "Shows a maintenance page to visitors and customers instead of the store. "
            "Signed-in staff keep full access, including this page."),
    Setting("maintenance_message", "maintenance", "Message shown to visitors", "textarea",
            "We're carrying out scheduled maintenance and will be back shortly. "
            "Thank you for your patience.", rows=3),
)

GROUPS: tuple[Group, ...] = (
    Group("store", "Store & Contact", "fa-store",
          "Name, contact details and links shown in the footer and on the contact page.",
          _STORE),
    Group("about", "About Page", "fa-circle-info",
          "The story, headline numbers and lead paragraph on the About page.",
          _ABOUT),
    Group("document", "Quote & Invoice", "fa-file-invoice",
          "The letterhead and wording on printed quotations, invoices and receipts.",
          _DOCUMENT),
    Group("payment", "KHQR Payments", "fa-money-bill-transfer",
          "What the payer's banking app shows, and how long a generated QR stays "
          "payable. Bank credentials stay in the server's environment.",
          _PAYMENT),
    Group("maintenance", "Maintenance", "fa-screwdriver-wrench",
          "Temporarily close the storefront without stopping the server.",
          _MAINTENANCE),
)

SETTINGS: dict[str, Setting] = {s.key: s for group in GROUPS for s in group.settings}

DEFAULTS: dict[str, Any] = {key: s.default for key, s in SETTINGS.items()}

PUBLIC_KEYS: frozenset[str] = frozenset(key for key, s in SETTINGS.items() if s.public)


class SettingError(ValueError):
    """A rejected value, with a message meant to be shown to the admin who typed it."""


def coerce(key: str, raw: Any) -> Any:
    """Validate and normalize one submitted value, or raise SettingError.

    Returns the value in its declared type - so `"true"` from an HTML checkbox becomes
    `True`, and `"30"` from a number input becomes `30`. The result is what gets stored
    as JSON, which is why this has to run before the write and not at read time.
    """
    if key not in SETTINGS:
        raise SettingError(f"Unknown setting '{key}'")
    spec = SETTINGS[key]

    if spec.type == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "on", "yes")
        return bool(raw)

    if spec.type == "number":
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise SettingError(f"{spec.label} must be a number")
        if spec.minimum is not None and value < spec.minimum:
            raise SettingError(f"{spec.label} must be at least {_plain(spec.minimum)}")
        if spec.maximum is not None and value > spec.maximum:
            raise SettingError(f"{spec.label} must be at most {_plain(spec.maximum)}")
        # Whole numbers come back as int so the UI doesn't render "30.0 days".
        return int(value) if value == int(value) else value

    # Everything else is text of some flavour. None is normalized to "" so "cleared" and
    # "never set" don't behave differently downstream (both mean "empty", and a setting
    # is reset to its default by deleting the row, not by writing null).
    value = "" if raw is None else str(raw).strip()

    if spec.type == "url" and value:
        # Anything that isn't http(s) can be a javascript:/data: payload, and these
        # values land in href/src attributes on a public page.
        if not value.startswith(("http://", "https://", "tel:", "mailto:")):
            raise SettingError(
                f"{spec.label} must start with http://, https://, tel: or mailto:"
            )
    if spec.type == "image" and value:
        # Normally written by POST /settings/image/{key}, which stores whatever
        # save_object() returned - an R2 URL or a local "/static/uploads/..." path.
        # A hand-written PUT could still put anything here, and the value lands in a
        # src= on the printed quote, so the same javascript:/data: guard as `url`
        # applies - plus the site-relative form the local-disk fallback produces.
        if not value.startswith(("http://", "https://", "/")):
            raise SettingError(
                f"{spec.label} must be an uploaded picture, or a full https:// URL"
            )
    if spec.type == "email" and value and "@" not in value:
        raise SettingError(f"{spec.label} must be an email address")
    if len(value) > 4000:
        raise SettingError(f"{spec.label} is too long (4000 characters max)")
    return value


def _plain(number: float) -> str:
    return str(int(number)) if number == int(number) else str(number)


def describe() -> list[dict]:
    """The spec as plain JSON, for the admin page to render its form from. Keeping the
    form generated from this (rather than hand-written HTML per setting) is what stops
    the page and the validation from disagreeing about what exists."""
    return [
        {
            "id": group.id,
            "label": group.label,
            "icon": group.icon,
            "blurb": group.blurb,
            "settings": [
                {
                    "key": s.key,
                    "label": s.label,
                    "type": s.type,
                    "default": s.default,
                    "help": s.help,
                    "minimum": s.minimum,
                    "maximum": s.maximum,
                    "rows": s.rows,
                }
                for s in group.settings
            ],
        }
        for group in GROUPS
    ]
