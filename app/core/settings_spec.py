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


@dataclass(frozen=True)
class Setting:
    key: str
    group: str
    label: str
    type: str  # text | textarea | url | email | number | bool
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
    Setting("receipt_note_khqr", "document", "Receipt note — paid by KHQR", "text",
            "Paid via KHQR. Thank you for your purchase.",
            "Replaces the validity line once an order is paid."),
    Setting("receipt_note_cash", "document", "Receipt note — paid in cash", "text",
            "Paid in full. Thank you for your purchase."),
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
    Group("document", "Quote & Invoice", "fa-file-invoice",
          "The letterhead and wording on printed quotations, invoices and receipts.",
          _DOCUMENT),
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
