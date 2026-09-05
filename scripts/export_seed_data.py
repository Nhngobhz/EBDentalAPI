"""
Dev helper: dump the CURRENT contents of the catalog tables back out as a
Python data module (scripts/seed_data.py), which scripts/seed_catalog.py then
replays into an empty database.

Usage:
    python -m scripts.export_seed_data
    (run from the project root, with the virtualenv activated and
     DATABASE_URL pointing at the instance you want to export)

Why a generated .py rather than `pg_dump --data-only`: the seed has to survive
being replayed into a database whose primary keys start over at 1, and it has
to stay reviewable in a diff. So every cross-table reference here is written as
a NATURAL KEY (brand name, category name, product name) and resolved on the way
back in - no id is ever exported, and re-exporting after an unrelated edit
produces a diff you can actually read.

What is NOT exported:
  * orders / order_items / pending_checkouts - transactional history, not
    fixtures. Replaying old quotes into a fresh database would be misleading.
  * ids, created_at, updated_at, updated_by_user_id - assigned by the database
    on insert, so seeding them would be a lie about who did what and when.
  * verification / reset tokens and last_login - per-instance auth state.

Passwords come across as the stored bcrypt hash (`hashed_password`), because a
hash is all the database has - the plaintext is not recoverable. Seeded logins
therefore keep working exactly as they do on the instance that was exported.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.database import SessionLocal
from app.models import (
    AppSetting,
    Brand,
    Category,
    Customer,
    HeroSlide,
    Manual,
    Product,
    ProductFreeItem,
    ProductImage,
    Promotion,
    PromotionItem,
    QrCode,
    Set,
    SetItem,
    SetOptionChoice,
    SetOptionGroup,
    User,
)

OUT_PATH = Path(__file__).with_name("seed_data.py")

HEADER = '''"""
Seed fixtures - GENERATED FILE, DO NOT EDIT BY HAND.

Exported from a live database on {stamp} by
`python -m scripts.export_seed_data`, and replayed by
`python -m scripts.seed_catalog`. Hand edits are lost the next time somebody
re-exports; change the data in the database and export again instead.

Every reference between tables is a natural key ("brand", "category",
"product" hold names, not ids) - see the module docstring in
scripts/export_seed_data.py for why.
"""
from datetime import datetime, timezone'''


# --------------------------------------------------------------------------
# Literal rendering
# --------------------------------------------------------------------------
def _num(value: Decimal | float | int | None):
    """Numeric(10, 2) -> the shortest Python number that round-trips into it.

    Decimal("3372.50") is written as 3372.5 and Decimal("2600.00") as 2600, so
    the generated file reads like the hand-written one it replaces rather than
    like a column dump.
    """
    if value is None:
        return None
    dec = Decimal(str(value))
    return int(dec) if dec == dec.to_integral_value() else float(dec)


def _lit(value) -> str:
    """One Python literal. Strings go through json.dumps for the escaping
    (compatible with Python's own string syntax) with ensure_ascii off, so
    non-ASCII product text stays readable in the diff."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, Decimal):
        return repr(_num(value))
    if isinstance(value, datetime):
        utc = value.astimezone(timezone.utc)
        return (
            f"datetime({utc.year}, {utc.month}, {utc.day}, {utc.hour}, "
            f"{utc.minute}, {utc.second}, tzinfo=timezone.utc)"
        )
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_lit(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_lit(k)}: {_lit(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"no literal form for {type(value).__name__}: {value!r}")


def _row(record: dict, indent: str = "    ") -> str:
    """One dict on one line, unless it carries a nested list of members - those
    get a line each so a bundle's contents stay legible."""
    nested = {k: v for k, v in record.items() if isinstance(v, list)}
    flat = {k: v for k, v in record.items() if k not in nested}

    parts = ", ".join(f"{_lit(k)}: {_lit(v)}" for k, v in flat.items())
    if not nested:
        return f"{indent}{{{parts}}},"

    out = [f"{indent}{{{parts},"]
    for key, members in nested.items():
        if not members:
            out.append(f'{indent}    "{key}": [],')
            continue
        out.append(f'{indent}    "{key}": [')
        for member in members:
            out.append(f"{indent}        {_lit(member)},")
        out.append(f"{indent}    ],")
    out.append(f"{indent}}},")
    return "\n".join(out)


def _block(name: str, records: list[dict], comment: str = "") -> str:
    lines = []
    if comment:
        lines.extend(f"# {line}" for line in comment.strip().splitlines())
    if not records:
        lines.append(f"{name} = []")
        return "\n".join(lines) + "\n"
    lines.append(f"{name} = [")
    lines.extend(_row(record) for record in records)
    lines.append("]")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Table -> records
# --------------------------------------------------------------------------
def export_brands(db) -> list[dict]:
    return [
        {"brand_name": b.brand_name, "brand_image": b.brand_image}
        for b in db.query(Brand).order_by(Brand.id).all()
    ]


def export_categories(db) -> list[dict]:
    return [
        {"category_name": c.category_name, "category_icon": c.category_icon}
        for c in db.query(Category).order_by(Category.id).all()
    ]


def export_products(db) -> list[dict]:
    # product_name is the natural key on the way back in, so a duplicate would
    # silently collapse two catalog rows into one. Caught here rather than
    # letting the seeder quietly skip the second one.
    names = [p.product_name for p in db.query(Product.product_name).all()]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise SystemExit(
            "Cannot export: these product names are not unique, so they can't be "
            "used as seed keys - rename one of each pair first:\n  "
            + "\n  ".join(dupes)
        )

    records = []
    for p in db.query(Product).order_by(Product.id).all():
        images = (
            db.query(ProductImage)
            .filter(ProductImage.product_id == p.id)
            .order_by(ProductImage.sort_order, ProductImage.id)
            .all()
        )
        free_items = (
            db.query(ProductFreeItem)
            .filter(ProductFreeItem.parent_product_id == p.id)
            .order_by(ProductFreeItem.id)
            .all()
        )
        records.append(
            {
                "product_name": p.product_name,
                "description": p.description,
                "price": _num(p.price),
                "list_price": _num(p.list_price),
                "discount": _num(p.discount),
                "discount_type": p.discount_type,
                "brand": p.brand.brand_name if p.brand else None,
                "category": p.category.category_name if p.category else None,
                "product_code": p.product_code,
                "uom": p.uom,
                "badge": p.badge,
                "section": p.section,
                "is_purchasable": p.is_purchasable,
                "product_image": p.product_image,
                # Gallery rows, photos and videos alike - each one carries its
                # own media_type so a re-seed cannot turn a clip into a photo
                # nothing can render. seed_catalog also accepts the older
                # bare-string form, so seed files exported before videos
                # existed still load.
                "images": [
                    {"image": img.image, "media_type": img.media_type}
                    for img in images
                ],
                "free_items": [
                    {"product": fi.product.product_name, "qty": fi.qty}
                    for fi in free_items
                    if fi.product
                ],
            }
        )
    return records


def export_manuals(db) -> list[dict]:
    return [
        {
            "product": m.product.product_name,
            "title": m.title,
            "description": m.description,
            "manual_image": m.manual_image,
            "pdf": m.pdf,
        }
        for m in db.query(Manual).order_by(Manual.id).all()
        if m.product
    ]


def export_promotions(db) -> list[dict]:
    records = []
    for promo in db.query(Promotion).order_by(Promotion.id).all():
        items = (
            db.query(PromotionItem)
            .filter(PromotionItem.promotion_id == promo.id)
            .order_by(PromotionItem.id)
            .all()
        )
        records.append(
            {
                "promotion_name": promo.promotion_name,
                "description": promo.description,
                "price": _num(promo.price),
                "old_price": _num(promo.old_price),
                "start_date": promo.start_date,
                "end_date": promo.end_date,
                "promotion_image": promo.promotion_image,
                "banner_image": promo.banner_image,
                "items": [
                    {"product": it.product.product_name, "qty": it.qty}
                    for it in items
                    if it.product
                ],
            }
        )
    return records


def export_sets(db) -> list[dict]:
    records = []
    for s in db.query(Set).order_by(Set.id).all():
        items = (
            db.query(SetItem).filter(SetItem.set_id == s.id).order_by(SetItem.id).all()
        )
        records.append(
            {
                "set_name": s.set_name,
                "description": s.description,
                "price": _num(s.price),
                "old_price": _num(s.old_price),
                # By name, like a product's brand - ids differ between databases.
                # None for a set filed under no brand.
                "brand": s.brand.brand_name if s.brand else None,
                "set_image": s.set_image,
                "detail_image": s.detail_image,
                # Swappable slots. price_delta stays None where it is derived,
                # so re-seeding keeps the "work it out from the products"
                # behaviour rather than freezing today's gap into the fixture.
                "option_groups": [
                    {
                        "name": g.name,
                        "choices": [
                            {
                                "product": c.product.product_name,
                                "qty": c.qty,
                                "price_delta": _num(c.price_delta),
                                "is_default": c.is_default,
                            }
                            for c in g.choices
                            if c.product
                        ],
                    }
                    for g in sorted(s.option_groups, key=lambda g: (g.sort_order, g.id))
                ],
                "items": [
                    {"product": it.product.product_name, "qty": it.qty}
                    for it in items
                    if it.product
                ],
            }
        )
    return records


def export_users(db) -> list[dict]:
    return [
        {
            "user_name": u.user_name,
            "email": u.email,
            "hashed_password": u.hashed_password,
            "role_title": u.role_title,
            "address": u.address,
            "phone_num": u.phone_num,
            "user_image": u.user_image,
            "date_of_birth": u.date_of_birth.isoformat() if u.date_of_birth else None,
            "gender": u.gender,
            "user_management": u.user_management,
            "price_listing": u.price_listing,
            "product_management": u.product_management,
            "customer_management": u.customer_management,
            "admin": u.admin,
            "is_active": u.is_active,
            "is_verified": u.is_verified,
        }
        for u in db.query(User).order_by(User.id).all()
    ]


def export_customers(db) -> list[dict]:
    return [
        {
            "customer_name": c.customer_name,
            "email": c.email,
            "hashed_password": c.hashed_password,
            "address": c.address,
            "phone_num": c.phone_num,
            "customer_image": c.customer_image,
            "latitude": _num(c.latitude),
            "longitude": _num(c.longitude),
            "map_link": c.map_link,
            "date_of_birth": c.date_of_birth.isoformat() if c.date_of_birth else None,
            "gender": c.gender,
            "access_permission": c.access_permission,
            "is_active": c.is_active,
            "is_verified": c.is_verified,
        }
        for c in db.query(Customer).order_by(Customer.id).all()
    ]


def export_app_settings(db) -> list[dict]:
    """The site-wide settings that have been overridden away from their spec
    default. Keyed by `key`, which is the table's own primary key, so no
    natural-key translation is needed. Rows for keys the spec no longer defines
    are exported as they are - the reader ignores them, same as the app does."""
    return [
        {"key": s.key, "value": s.value}
        for s in db.query(AppSetting).order_by(AppSetting.key).all()
    ]


def export_qr_codes(db) -> list[dict]:
    """The contact page's department QR cards. `title` is the natural key on the
    way back in."""
    return [
        {
            "title": q.title,
            "subtitle": q.subtitle,
            "qr_image": q.qr_image,
            "badge_label": q.badge_label,
            "badge_variant": q.badge_variant,
            "badge_icon": q.badge_icon,
            "sort_order": q.sort_order,
        }
        for q in db.query(QrCode).order_by(QrCode.sort_order, QrCode.id).all()
    ]


def export_hero_slides(db) -> list[dict]:
    """The storefront hero carousel. `heading` is the natural key - two slides
    with the same heading would be indistinguishable to a reader anyway. The
    promotion-derived first slide is not here: the template builds that one from
    the live Promotion, it is not a row."""
    return [
        {
            "heading": h.heading,
            "heading_highlight": h.heading_highlight,
            "subheading": h.subheading,
            "slide_image": h.slide_image,
            "badge_label": h.badge_label,
            "badge_icon": h.badge_icon,
            "button_label": h.button_label,
            "button_url": h.button_url,
            "is_active": h.is_active,
            "sort_order": h.sort_order,
        }
        for h in db.query(HeroSlide).order_by(HeroSlide.sort_order, HeroSlide.id).all()
    ]


def main() -> None:
    db = SessionLocal()
    try:
        brands = export_brands(db)
        categories = export_categories(db)
        products = export_products(db)
        manuals = export_manuals(db)
        promotions = export_promotions(db)
        sets = export_sets(db)
        users = export_users(db)
        customers = export_customers(db)
        app_settings = export_app_settings(db)
        qr_codes = export_qr_codes(db)
        hero_slides = export_hero_slides(db)
    finally:
        db.close()

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    chunks = [
        HEADER.format(stamp=stamp),
        _block("BRANDS", brands),
        _block("CATEGORIES", categories),
        _block(
            "PRODUCTS",
            products,
            '"brand" / "category" are names resolved against BRANDS / CATEGORIES above.\n'
            '"images" are the EXTRA gallery items only - photos AND videos, each\n'
            'tagged with its media_type. The primary picture is product_image.\n'
            '"free_items" name other products in this same list, so\n'
            "they are linked in a second pass once every product exists.",
        ),
        _block("MANUALS", manuals),
        _block("PROMOTIONS", promotions),
        _block("SETS", sets),
        _block(
            "USERS",
            users,
            "hashed_password is the bcrypt hash straight from the source database -\n"
            "the plaintext is not recoverable, so seeded accounts keep whatever\n"
            "password they had there.",
        ),
        _block("CUSTOMERS", customers),
        _block(
            "APP_SETTINGS",
            app_settings,
            "Overrides only. A key absent here reads as its default from\n"
            "app/core/settings_spec.py, so this block being short is normal.",
        ),
        _block("QR_CODES", qr_codes),
        _block("HERO_SLIDES", hero_slides),
    ]
    OUT_PATH.write_text("\n\n".join(chunks), encoding="utf-8")

    print(f"Wrote {OUT_PATH}")
    for label, rows in [
        ("brands", brands),
        ("categories", categories),
        ("products", products),
        ("manuals", manuals),
        ("promotions", promotions),
        ("sets", sets),
        ("users", users),
        ("customers", customers),
        ("app settings", app_settings),
        ("qr codes", qr_codes),
        ("hero slides", hero_slides),
    ]:
        print(f"  {len(rows):>4} {label}")


if __name__ == "__main__":
    main()
