"""
Bring the SAP-sourced catalogues in our Postgres into line with SAP.

Reads the SAP company database (read-only, via scripts/sap_db_pull) and upserts
what it finds into `products`. SAP is the authority for the item master; this
database is the authority for everything about presenting it.

Two catalogues, defined in scripts/sap_db_pull.CATALOGUES:

    materials    SAP groups 101 + 106  ->  products.section = "materials"
    spare-parts  SAP group  103        ->  products.section = "spare_parts"

They are separate sections rather than one, and spare parts are NOT stored as
"machinery" even though that is the shop they are sold in. The reason is
delisting: this script hides everything in the section it owns that SAP has
stopped listing, so a spare-parts run that owned "machinery" would delist all 110
hand-curated machines, none of which SAP has ever heard of. A section per
catalogue makes each run's authority exactly the rows it is actually the authority
for. See models.Product.section.

What the sync owns, and what it must never touch
------------------------------------------------
Owned - overwritten on every run:
    product_name, list_price, uom, brand_id, category_id, stock_qty,
    stock_synced_at, section

Never written, at all:
    product_image, the product_images gallery, description, badge,
    is_purchasable, discount, discount_type

That split is the whole design. Photos are uploaded here and exist nowhere in SAP,
so a sync that wrote `product_image` would erase every picture anyone had added the
moment it ran - the single most destructive thing this script could do, and the
reason the owned-field list is written out explicitly rather than inferred from
"whatever the extract happens to contain".

Prices deserve the same care in the other direction. SAP's "Normal Sale Price"
lands on `list_price`, never directly on `price`, and `price` is then recomputed
from whatever discount staff have attached here. So SAP repricing an item keeps any
promotion running on top of it, instead of silently cancelling it.

Matching
--------
On `products.product_code` = SAP `OITM.ItemCode`, which is unique on both sides. A
code that already exists as a *machinery* product is reported and skipped, never
converted - machinery is maintained by hand and never enters SAP, so a collision
there means the code was reused, not that the product moved.

Names
-----
`product_name` is OITM.ItemName, except on the rows where SAP has flattened every
non-ASCII character in that column to a literal '?'. Those fall back to FrgnName,
which kept the real text - see _display_name, and the section the run report grows
listing exactly which rows it had to do that for.

Usage:
    python -m scripts.sap_sync                       # dry run, both catalogues
    python -m scripts.sap_sync --apply               # actually write, both
    python -m scripts.sap_sync --catalogue spare-parts --apply
    python -m scripts.sap_sync --catalogue materials --from-file         sap_extract/materials.json --apply
    python -m scripts.sap_sync --transport local --apply    # on the server
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.database import SessionLocal
from app.models import Brand, Category, Product

# Imported for its import side effect, and it is load-bearing: app/core/activity.py
# registers the before_flush/after_flush listeners that write the change log, and a
# listener that is never imported is never registered. The FastAPI app gets this
# for free through main.py; a standalone script does not, and the failure is
# invisible - the sync runs perfectly and simply writes no history at all. A run
# that reprices half the catalogue with no record of having done so is exactly what
# the change log exists to prevent, so the import stays even though nothing below
# references the module.
#
# scripts/seed_catalog.py deliberately does NOT do this: seeding an empty dev
# database is not a change anyone made. A recurring sync against the live catalogue
# is.
import app.core.activity  # noqa: F401

from scripts.sap_db_pull import (
    CATALOGUES,
    GROUP_NAMES,
    TRANSPORTS,
    QueryFailed,
    TransportUnavailable,
    build_query,
    _run_sql,
)

# products.brand_id is NOT NULL, and roughly a fifth of the SAP items have no
# U_Brand at all. They need somewhere to go that is honest about what it means -
# an existing real brand would be a lie, and skipping the items would drop 1,600
# sellable products off the site.
FALLBACK_BRAND = "Unbranded"

# SAP has a currency literally named "$", separate from its "USD", and exactly one
# item is priced in it. It is *probably* someone picking the wrong entry from the
# dropdown, but "probably" is not good enough for money: an item imported at the
# wrong currency is mispriced on a live storefront with nothing to show for it. So
# only USD is accepted and anything else is reported by name for a human to fix in
# SAP - a one-field correction there, after which the item syncs normally.
ACCEPTED_CURRENCIES = {"USD"}

# The largest share of one catalogue that a single run may hide before it stops and
# asks. Withdrawals come in ones and tens; a run that wants to delist a fifth of the
# catalogue has almost certainly read a partial extract - see _apply_delisting.
DEFAULT_MAX_DELIST_RATIO = 0.10


class Report:
    """Counts and, more usefully, the specific rows that need a person to look."""

    def __init__(self, label: str = "catalogue") -> None:
        self.label = label
        self.created: list[str] = []
        self.updated: list[tuple[str, list[str]]] = []
        self.unchanged = 0
        self.skipped_no_price: list[str] = []
        self.skipped_currency: list[tuple[str, str]] = []
        self.skipped_foreign: list[tuple[str, str]] = []
        self.new_brands: list[str] = []
        self.new_categories: list[str] = []
        self.delisted: list[tuple[str, str]] = []
        self.relisted: list[str] = []
        self.names_from_foreign: list[tuple[str, str]] = []
        self.names_unrecovered: list[str] = []

    def render(self, applied: bool) -> str:
        mode = "APPLIED" if applied else "DRY RUN - nothing was written"
        L = [f"# SAP {self.label} sync - {mode}", ""]
        L.append(f"- Created: **{len(self.created)}**")
        L.append(f"- Updated: **{len(self.updated)}**")
        L.append(f"- Unchanged: {self.unchanged}")
        L.append(f"- New brands: {len(self.new_brands)}")
        L.append(f"- New categories: {len(self.new_categories)}")
        L.append(f"- Names read from FrgnName: {len(self.names_from_foreign)}")
        L.append(f"- **Delisted: {len(self.delisted)}** (hidden from the storefront)")
        L.append(f"- Re-listed: {len(self.relisted)}")
        L.append("")

        if self.delisted:
            L.append("## Delisted")
            L.append("")
            L.append(
                "SAP no longer offers these. They are hidden from the storefront but "
                "kept as rows - they appear on past orders, and deleting them would "
                "blank those order lines."
            )
            L.append("")
            for code, why in self.delisted[:40]:
                L.append(f"- `{code}` - {why}")
            if len(self.delisted) > 40:
                L.append(f"- _...{len(self.delisted) - 40} more_")
            L.append("")

        if self.relisted:
            L.append("## Back in SAP")
            L.append("")
            L.append(", ".join(f"`{c}`" for c in self.relisted[:40]))
            L.append("")

        skipped = (
            len(self.skipped_no_price) + len(self.skipped_currency) + len(self.skipped_foreign)
        )
        L.append(f"## Skipped ({skipped})")
        L.append("")
        if self.skipped_no_price:
            L.append(f"**No price on list 1 ({len(self.skipped_no_price)})** - "
                     "`price` is required and must be > 0:")
            L.extend(f"  - {c}" for c in self.skipped_no_price[:20])
        if self.skipped_currency:
            L.append(f"**Not priced in USD ({len(self.skipped_currency)})** - "
                     "fix the currency in SAP, then re-run:")
            L.extend(f"  - {c} (currency `{cur}`)" for c, cur in self.skipped_currency[:20])
        if self.skipped_foreign:
            L.append(f"**Code already used by a product in another catalogue "
                     f"({len(self.skipped_foreign)})** - left untouched:")
            L.extend(f"  - {c} (currently in `{sec}`)" for c, sec in self.skipped_foreign[:20])
        if not skipped:
            L.append("_Nothing skipped._")
        L.append("")

        if self.names_from_foreign or self.names_unrecovered:
            L.append(f"## Names read from FrgnName ({len(self.names_from_foreign)})")
            L.append("")
            L.append(
                "ItemName holds literal '?' for these - every non-ASCII character in "
                "that column was flattened when it was written, and nothing can undo it "
                "from here. FrgnName kept the real text, so it was used instead. Fixing "
                "ItemName in SAP is what makes this section go away."
            )
            L.append("")
            for code, name in self.names_from_foreign[:20]:
                L.append(f"- `{code}` -> {name}")
            if len(self.names_from_foreign) > 20:
                L.append(f"- _...{len(self.names_from_foreign) - 20} more_")
            L.append("")
            if self.names_unrecovered:
                L.append(
                    f"**Still unreadable ({len(self.names_unrecovered)})** - FrgnName is "
                    "empty or carries '?' of its own, so the name is stored as it stands. "
                    "Only a person editing SAP can fix these:"
                )
                L.extend(f"  - {c}" for c in self.names_unrecovered[:20])
                L.append("")

        if self.updated:
            L.append("## Changed fields")
            L.append("")
            for code, fields in self.updated[:40]:
                L.append(f"- `{code}`: {', '.join(fields)}")
            if len(self.updated) > 40:
                L.append(f"- _...{len(self.updated) - 40} more_")
            L.append("")
        return "\n".join(L) + "\n"


def _norm(value: str | None) -> str | None:
    """Trim, and treat an empty string as absent."""
    return (value or "").strip() or None


# Zero-width characters that ride along in FrgnName, used there as Khmer word
# separators. Dropping them changes nothing on screen - they have no width - but
# one sitting inside `5/1<U+200B>6''` is enough that a search for "5/16" never
# matches the row, so they come out on the way in.
_ZERO_WIDTH = str.maketrans({"​": None, "﻿": None})


def _display_name(row: dict) -> tuple[str, str]:
    """The name to store, and where it came from: "item", "foreign" or "damaged".

    SAP's OITM.ItemName has lost every non-ASCII character it ever held - each one
    flattened to a literal '?', the signature of a Unicode value written through a
    non-Unicode path. This is not a rendering problem at either end: the question
    marks are really in the column, 211 of the 8,127 material items carry them
    (`????????? 4.0cm K30290`), and no reader can recover the original text.

    OITM.FrgnName was written through a Unicode-safe path and did keep it - both the
    Khmer and, on 22 further rows, a zero-width space whose loss leaves a lone stray
    '?' mid-name. So FrgnName is read wherever ItemName is visibly damaged. The two
    hold the same product's name, which makes the swap a repair rather than a change
    of meaning, and it stays limited to damaged rows because ItemName is otherwise
    the field SAP treats as authoritative and the one staff recognise.

    A '?' is the whole test. No genuine name in this catalogue contains one, and if
    a future one did, FrgnName still names the same product - so the false positive
    costs nothing, while parsing for "looks like mojibake" would miss rows.

    The real fix is in SAP, and the sync report names the rows so someone can make
    it. Until then this keeps the storefront readable. Rows FrgnName cannot save are
    reported rather than quietly shipped.
    """
    name = _norm(row.get("name")) or ""
    if "?" not in name:
        # Six ItemName values kept a zero-width space of their own, which is how it
        # is clear the flattening ran over some rows and not others. Spacing is
        # otherwise left exactly as SAP has it: collapsing double spaces here would
        # rename 305 undamaged products for nothing anyone can see, burying the
        # handful of real changes in every future run's change log.
        return name.translate(_ZERO_WIDTH), "item"
    foreign = _norm(row.get("foreign_name"))
    if foreign is None or "?" in foreign:
        return name, "damaged"
    # Whitespace is re-collapsed on this path only, because removing a zero-width
    # space that sat beside a real one is what leaves the double.
    return " ".join(foreign.translate(_ZERO_WIDTH).split()), "foreign"


class NameCache:
    """Get-or-create for Brand/Category, matched case-insensitively.

    Case-insensitivity is the point. SAP compares these names under a
    case-insensitive collation, so "Woodpecker" and "WOODPECKER" are one brand
    there; `brands.brand_name` is UNIQUE under Postgres' default case-*sensitive*
    collation, so importing both would create two - and nineteen real brands in
    this data are spelled both ways. Whichever spelling is seen first wins, which
    is arbitrary but at least consistent, and leaves a single row to rename.
    """

    def __init__(self, db, model, name_attr: str, created: list[str]):
        self._db = db
        self._model = model
        self._attr = name_attr
        self._created = created
        self._by_fold = {
            getattr(row, name_attr).casefold(): row for row in db.query(model).all()
        }

    def get_or_create(self, name: str | None):
        name = _norm(name)
        if name is None:
            return None
        existing = self._by_fold.get(name.casefold())
        if existing is not None:
            return existing
        row = self._model(**{self._attr: name})
        self._db.add(row)
        # Flushed immediately so the row has an id to assign to products in this
        # same pass. Without it, 8,000 products would all be holding unflushed
        # objects and the first one to reference a brand would trigger the flush
        # anyway - at a less predictable moment.
        self._db.flush()
        self._by_fold[name.casefold()] = row
        self._created.append(name)
        return row


def _price_for(list_price: Decimal, discount, discount_type: str) -> Decimal:
    """The charged price implied by a SAP list price and the discount held here.

    Mirrors _derive_list_price in routers/products.py, run backwards: that derives
    the "was" figure from the charged one, this derives the charged one from the
    "was". Kept in step deliberately - if the two disagree, a synced product shows
    a discount that does not match the gap between its own two prices.
    """
    discount = Decimal(discount or 0)
    if discount <= 0:
        return list_price
    if discount_type == "cash":
        charged = list_price - discount
    else:
        charged = list_price * (Decimal(100) - discount) / Decimal(100)
    # price has a `> 0` constraint in the schema, and a cash discount left over
    # from a higher list price can exceed it. Falling back to the list price (i.e.
    # dropping the stale discount) beats writing a negative or zero price.
    return charged.quantize(Decimal("0.01")) if charged > 0 else list_price


def sync(db, rows: list[dict], report: Report, now: datetime, section: str,
         max_delist_ratio: float = DEFAULT_MAX_DELIST_RATIO) -> None:
    """Upsert one catalogue's extract into `products`, then delist what SAP dropped.

    `section` is both where new rows land and the limit of this run's authority:
    nothing outside it is written, and nothing outside it is delisted."""
    brands = NameCache(db, Brand, "brand_name", report.new_brands)
    categories = NameCache(db, Category, "category_name", report.new_categories)
    fallback_brand = brands.get_or_create(FALLBACK_BRAND)

    # One query instead of 8,000: the extract is the whole catalogue, so a
    # per-item lookup would dominate the runtime of the sync.
    existing = {
        p.product_code: p
        for p in db.query(Product).filter(Product.product_code.isnot(None)).all()
    }

    # Codes SAP still offers, and the ones it has stopped offering with the reason.
    # Built during the pass and acted on afterwards, because "SAP no longer has it"
    # can only be concluded once the whole extract has been read.
    offered: set[str] = set()
    withdrawn: dict[str, str] = {}

    for row in rows:
        code = _norm(row.get("code"))
        if code is None:
            continue

        # Whether SAP still sells it, decided before anything about price. An item
        # can be perfectly valid and temporarily unpriced (see below) - that is a
        # data gap, not a withdrawal, and must not hide it from the storefront.
        if not row.get("valid"):
            withdrawn[code] = "marked invalid in SAP"
            continue
        if row.get("frozen"):
            withdrawn[code] = "frozen in SAP"
            continue
        offered.add(code)

        price = row.get("price")
        if price is None or Decimal(price) <= 0:
            report.skipped_no_price.append(code)
            continue
        currency = _norm(row.get("currency"))
        if currency not in ACCEPTED_CURRENCIES:
            report.skipped_currency.append((code, currency or "none"))
            continue

        product = existing.get(code)
        if product is not None and product.section != section:
            # The code is already held by a product in another catalogue. Never
            # converted, only reported: a hand-curated machinery product carries
            # photographs, a description and a price staff set, and moving it into
            # a synced section would hand all of that to SAP. A collision means the
            # code was reused, not that the product changed catalogue.
            report.skipped_foreign.append((code, product.section))
            continue

        list_price = Decimal(price).quantize(Decimal("0.01"))
        name, name_source = _display_name(row)
        if name_source == "foreign":
            report.names_from_foreign.append((code, name))
        elif name_source == "damaged":
            report.names_unrecovered.append(code)
        brand = brands.get_or_create(row.get("brand")) or fallback_brand
        category = categories.get_or_create(row.get("subgroup"))
        # OITM.OnHand is the total across warehouses; the extract confirms it equals
        # the sum of the per-warehouse rows, so there is nothing to add up here.
        stock = Decimal(row.get("on_hand") or 0).quantize(Decimal("0.01"))

        if product is None:
            product = Product(
                product_code=code,
                product_name=name,
                section=section,
                list_price=list_price,
                price=list_price,
                discount=Decimal("0"),
                discount_type="percent",
                uom=_norm(row.get("uom")),
                brand_id=brand.id,
                category_id=category.id if category else None,
                stock_qty=stock,
                stock_synced_at=now,
            )
            db.add(product)
            existing[code] = product
            report.created.append(code)
            continue

        # An update touches only the owned fields, and records which ones actually
        # moved - "updated 8,127 products" every night would say nothing, while a
        # list of the forty whose price changed is worth reading.
        changed: list[str] = []
        wanted = {
            "product_name": name,
            "list_price": list_price,
            "price": _price_for(list_price, product.discount, product.discount_type),
            "uom": _norm(row.get("uom")),
            "brand_id": brand.id,
            "category_id": category.id if category else None,
            "stock_qty": stock,
        }
        for field, value in wanted.items():
            if getattr(product, field) != value:
                setattr(product, field, value)
                changed.append(field)

        # Stamped whether or not anything moved: the figure was confirmed against
        # SAP just now, and that is what this column records. It is left out of
        # `changed` so a run that only re-confirms stock still reads as unchanged.
        product.stock_synced_at = now

        if changed:
            report.updated.append((code, changed))
        else:
            report.unchanged += 1

    _apply_delisting(existing, offered, withdrawn, report, now, section, max_delist_ratio)


def _apply_delisting(existing, offered, withdrawn, report, now, section, max_delist_ratio):
    """Hide items SAP has stopped offering, and un-hide any that came back.

    Strictly the one section this run owns. The hand-curated machinery catalogue
    never enters SAP, so "absent from the extract" says nothing at all about it and
    delisting one of its products would be pure damage - and the same holds between
    the two SAP catalogues, since a materials run reads no spare parts and a
    spare-parts run reads no materials. Scoping by section is what stops each run
    concluding that the other's catalogue has been withdrawn.
    """
    ours = {code: p for code, p in existing.items() if p.section == section}
    stale = [code for code in ours if code not in offered]

    # The safety rail, and the reason this is a function rather than four lines
    # inline. This job is meant to run unattended: if a query returns a short
    # result - a partial extract, a timeout, someone syncing with the wrong
    # --groups - then "everything SAP no longer offers" is suddenly the whole
    # catalogue, and one silent run empties the storefront. A real withdrawal is a
    # handful of items; anything on the scale of the catalogue is a broken read
    # until a human says otherwise.
    if ours and len(stale) > len(ours) * max_delist_ratio:
        raise SystemExit(
            f"Refusing to delist {len(stale)} of {len(ours)} products in section "
            f"'{section}' ({len(stale) / len(ours):.0%}) - that looks like a partial "
            f"extract rather than {len(stale)} withdrawals. Re-run with "
            f"--max-delist-ratio 1.0 if it really is correct."
        )

    for code, product in ours.items():
        if code in offered:
            # Back in SAP: a freeze that has been lifted un-hides itself, instead
            # of waiting for someone to notice it ever happened.
            if product.delisted_at is not None:
                product.delisted_at = None
                report.relisted.append(code)
        elif product.delisted_at is None:
            product.delisted_at = now
            report.delisted.append(
                (code, withdrawn.get(code, "no longer in the SAP item master"))
            )


def run_catalogue(name: str, args, now: datetime) -> Report:
    """Read one catalogue's extract and sync it, in a transaction of its own.

    A session per catalogue rather than one spanning both, so a spare-parts run
    that trips the delisting rail cannot roll back a materials run that had already
    completed correctly - and so the two reports describe what was really written.
    """
    catalogue = CATALOGUES[name]
    section = catalogue["section"]

    if args.from_file:
        print(f"Reading {args.from_file}...", flush=True)
        payload = Path(args.from_file).read_text(encoding="utf-8")
    else:
        groups = ", ".join(f"{c} {GROUP_NAMES.get(c, '?')}" for c in catalogue["groups"])
        print(f"Reading SAP {name} ({groups})...", flush=True)
        payload = _run_sql(build_query(catalogue["groups"]), args.transport)

    # parse_float=Decimal: these are prices. Letting them become binary floats and
    # converting to Decimal afterwards is how 15.00 turns into 14.999999999999998
    # on a Numeric column.
    rows = json.loads(payload, parse_float=Decimal)
    for row in rows:
        raw = row.get("stock")
        if isinstance(raw, str):
            row["stock"] = json.loads(raw, parse_float=Decimal)
    if not rows:
        print(f"{name}: extract is empty - refusing to run.", file=sys.stderr)
        sys.exit(1)
    print(f"{len(rows)} SAP items.", flush=True)

    report = Report(catalogue["label"].lower())
    db = SessionLocal()
    # Attribute the change log to the sync rather than to a person. set_actor()
    # only takes a User or a Customer, and neither is true here - writing the
    # underlying key directly keeps actor_type "system" (which is accurate) while
    # still labelling every row, so a surprise price change is traceable to a run
    # of this script instead of appearing to come from nowhere. The catalogue is
    # named in the label because two jobs now write through this path, and "which
    # sync repriced this" is the first question anyone asks of the log.
    db.info["activity_actor"] = ("system", None, f"SAP sync ({name})")
    try:
        sync(db, rows, report, now, section, args.max_delist_ratio)
        if args.apply:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return report


def main() -> None:
    # The run report quotes item names, and the ones recovered from FrgnName are in
    # Khmer. Windows hands a bare console (and the scheduled task's redirected log)
    # a cp1252 stdout, which cannot encode them - printing the report then raises
    # UnicodeEncodeError *after* the catalogue has already been committed, so the
    # write succeeds while the process exits non-zero and Task Scheduler reports a
    # failure that did not happen. With two catalogues it is worse: the crash
    # printing the first report means the second never runs at all. errors=replace
    # rather than a narrower fix because a report is a thing to read, not to parse -
    # a '?' in the terminal costs nothing, and the file on disk is UTF-8 regardless.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # already wrapped, or not a TextIO
            pass

    parser = argparse.ArgumentParser(description="Sync SAP catalogues into Postgres.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write. Without it the sync runs in full and reports what it "
             "would do, but rolls back - which is how you check a run before trusting it.",
    )
    parser.add_argument(
        "--catalogue",
        choices=[*sorted(CATALOGUES), "all"],
        default="all",
        help="Which catalogue to sync (default: %(default)s). "
             + "; ".join(
                 f"{k}=groups {c['groups']} -> section '{c['section']}'"
                 for k, c in sorted(CATALOGUES.items())
             ),
    )
    parser.add_argument(
        "--from-file",
        help="Read a previously saved extract instead of querying SAP "
             "(e.g. sap_extract/spare_parts.json). Needs an explicit --catalogue: "
             "a file on disk does not say which section its rows belong in, and "
             "guessing would write a whole catalogue into the wrong one.",
    )
    parser.add_argument(
        "--transport",
        choices=TRANSPORTS,
        default="auto",
        help="How to reach SQL Server - see scripts/sap_db_pull.py. Use 'local' "
             "when running this on the server itself, which is what the scheduled "
             "production run does.",
    )
    parser.add_argument(
        "--max-delist-ratio",
        type=float,
        default=DEFAULT_MAX_DELIST_RATIO,
        help="Abort rather than delist more than this share of a catalogue in one "
             "run (default %(default)s). Pass 1.0 to allow any.",
    )
    parser.add_argument(
        "--out-dir",
        default="sap_extract",
        help="Where the per-catalogue run reports are written (default: %(default)s).",
    )
    args = parser.parse_args()

    if args.from_file and args.catalogue == "all":
        parser.error(
            "--from-file syncs exactly one catalogue - pass --catalogue "
            f"{' or --catalogue '.join(sorted(CATALOGUES))} to say which."
        )

    names = sorted(CATALOGUES) if args.catalogue == "all" else [args.catalogue]

    # One timestamp for the whole invocation, so stock_synced_at and any delisted_at
    # written tonight agree across catalogues instead of differing by the seconds
    # the first query happened to take.
    now = datetime.now(timezone.utc)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        try:
            report = run_catalogue(name, args, now)
        except (TransportUnavailable, QueryFailed) as exc:
            # The two failures that are about the machine rather than the data: the
            # wrong tools installed, or the database refusing the account the sync ran
            # as. Exit on the sentence each carries, so the admin panel - which reports
            # a failed run by showing the last line of output - says something
            # actionable instead of the tail of a traceback.
            sys.exit(str(exc))
        text = report.render(applied=args.apply)
        out_path = out_dir / f"{name.replace('-', '_')}_sync_report.md"
        out_path.write_text(text, encoding="utf-8")
        print()
        print(text)
        print(f"Report: {out_path}")
    if not args.apply:
        print("Dry run - re-run with --apply to write.")


if __name__ == "__main__":
    main()
