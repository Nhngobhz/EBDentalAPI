"""
Dev helper: fill an empty database with the catalog, bypassing the API -
brands, categories, products (with their gallery photos and free-with-purchase
items), manuals, promotions, sets, staff users and customers.

Usage:
    python -m scripts.seed_catalog
    (run from the project root, with the virtualenv activated and
     DATABASE_URL pointing at your Postgres instance)

The data itself lives in scripts/seed_data.py, which is GENERATED - it is a
dump of a real database taken by `python -m scripts.export_seed_data`, so the
seed stays in step with the live catalog instead of drifting away from it as a
hand-maintained list would. To refresh the fixtures, edit the database and
re-export; don't edit seed_data.py.

Safe to re-run: every row is matched by its natural unique key (name, email,
...) and skipped if it already exists, so this won't create duplicates on a
second run. Note that "skipped" means exactly that - an existing row is left
alone, never updated to match the seed. Re-running over a database you have
since edited will not reset it; drop and recreate for that.

Seeded login credentials (dev only - never use these in production):
    admin@store.dev        / Admin@12345       (all 4 permissions)
    staff@store.dev        / Staff@12345       (permission flags per the export)
    customer@store.dev     / Customer@12345    (access_permission=False, sees no prices)
    vip.customer@store.dev / VipCustomer@12345 (access_permission=True, sees prices)

    Passwords are seeded as the bcrypt hashes captured at export time, so these
    only hold as long as nobody changed them on the exported instance.

    NOTE: ".local" was originally used here but is rejected by pydantic's
    EmailStr (email-validator treats it as a reserved special-use TLD, see
    RFC 6762) - that only shows up once you try to actually log in with a
    seeded account, not in the test suite (which uses .example.com
    addresses), so ".dev" is used instead.
"""
from datetime import date

from app.database import SessionLocal
from app.models import (
    Brand,
    Category,
    Customer,
    Manual,
    Product,
    ProductFreeItem,
    ProductImage,
    Promotion,
    PromotionItem,
    Set,
    SetItem,
    SetOptionChoice,
    SetOptionGroup,
    User,
)
from scripts import seed_data


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def seed_brands(db) -> dict[str, Brand]:
    brand_by_name = {}
    for item in seed_data.BRANDS:
        name = item["brand_name"]
        brand = db.query(Brand).filter(Brand.brand_name == name).first()
        if brand:
            print(f"Brand '{name}' already exists (id={brand.id}), skipping.")
        else:
            brand = Brand(brand_name=name, brand_image=item.get("brand_image"))
            db.add(brand)
            db.flush()
            print(f"Created brand '{name}' (id={brand.id}).")
        brand_by_name[name] = brand
    return brand_by_name


def seed_categories(db) -> dict[str, Category]:
    category_by_name = {}
    for item in seed_data.CATEGORIES:
        name = item["category_name"]
        category = db.query(Category).filter(Category.category_name == name).first()
        if category:
            print(f"Category '{name}' already exists (id={category.id}), skipping.")
        else:
            category = Category(
                category_name=name, category_image=item.get("category_image")
            )
            db.add(category)
            db.flush()
            print(f"Created category '{name}' (id={category.id}).")
        category_by_name[name] = category
    return category_by_name


def seed_products(
    db, brand_by_name: dict[str, Brand], category_by_name: dict[str, Category]
) -> dict[str, Product]:
    """Products and their gallery images. Free-with-purchase links are NOT
    done here - they point at other products in the same list, which may not
    exist yet, so they get their own pass afterwards."""
    product_by_name = {}

    for item in seed_data.PRODUCTS:
        name = item["product_name"]
        product = db.query(Product).filter(Product.product_name == name).first()
        if product:
            print(f"Product '{name}' already exists (id={product.id}), skipping.")
            product_by_name[name] = product
            continue

        brand = brand_by_name.get(item.get("brand"))
        if brand is None:
            print(f"Brand '{item.get('brand')}' not found, skipping product '{name}'.")
            continue
        category = category_by_name.get(item.get("category"))

        product = Product(
            product_name=name,
            description=item.get("description"),
            price=item["price"],
            list_price=item["list_price"],
            discount=item["discount"],
            discount_type=item["discount_type"],
            brand_id=brand.id,
            category_id=category.id if category else None,
            product_code=item.get("product_code"),
            uom=item.get("uom"),
            badge=item.get("badge"),
            # Defaults True for a seed file exported before this column existed.
            is_purchasable=item.get("is_purchasable", True),
            product_image=item.get("product_image"),
        )
        db.add(product)
        db.flush()
        product_by_name[name] = product
        print(f"Created product '{product.product_name}' (id={product.id}).")

        for position, image in enumerate(item.get("images") or []):
            db.add(
                ProductImage(product_id=product.id, image=image, sort_order=position)
            )
        db.flush()

    return product_by_name


def seed_product_free_items(db, product_by_name: dict[str, Product]) -> None:
    for item in seed_data.PRODUCTS:
        parent = product_by_name.get(item["product_name"])
        if parent is None:
            continue

        for member in item.get("free_items") or []:
            free_product = product_by_name.get(member["product"])
            if free_product is None:
                print(
                    f"Product '{member['product']}' not found, skipping free item on "
                    f"'{item['product_name']}'."
                )
                continue

            existing = (
                db.query(ProductFreeItem)
                .filter(
                    ProductFreeItem.parent_product_id == parent.id,
                    ProductFreeItem.product_id == free_product.id,
                )
                .first()
            )
            if existing:
                print(
                    f"Free item '{member['product']}' on '{item['product_name']}' "
                    "already exists, skipping."
                )
                continue

            db.add(
                ProductFreeItem(
                    parent_product_id=parent.id,
                    product_id=free_product.id,
                    qty=member.get("qty", 1),
                )
            )
            db.flush()
            print(
                f"Linked free item '{member['product']}' to '{item['product_name']}'."
            )


def seed_manuals(db, product_by_name: dict[str, Product]) -> None:
    for item in seed_data.MANUALS:
        product = product_by_name.get(item["product"])
        if not product:
            print(f"Product '{item['product']}' not found, skipping manual.")
            continue

        existing = (
            db.query(Manual)
            .filter(Manual.product_id == product.id, Manual.description == item["description"])
            .first()
        )
        if existing:
            print(f"Manual for '{item['product']}' already exists (id={existing.id}), skipping.")
            continue

        manual = Manual(
            product_id=product.id,
            description=item.get("description"),
            manual_image=item.get("manual_image"),
            pdf=item.get("pdf"),
        )
        db.add(manual)
        db.flush()
        print(f"Created manual for '{item['product']}' (id={manual.id}).")


def seed_promotions(db, product_by_name: dict[str, Product]) -> None:
    for item in seed_data.PROMOTIONS:
        name = item["promotion_name"]
        existing = db.query(Promotion).filter(Promotion.promotion_name == name).first()
        if existing:
            print(f"Promotion '{name}' already exists (id={existing.id}), skipping.")
            continue

        promotion = Promotion(
            promotion_name=name,
            description=item.get("description"),
            price=item["price"],
            old_price=item.get("old_price"),
            start_date=item["start_date"],
            end_date=item["end_date"],
            promotion_image=item.get("promotion_image"),
        )
        db.add(promotion)
        db.flush()
        print(f"Created promotion '{promotion.promotion_name}' (id={promotion.id}).")

        for member in item.get("items") or []:
            product = product_by_name.get(member["product"])
            if product is None:
                print(f"Product '{member['product']}' not found, skipping promotion item.")
                continue
            db.add(
                PromotionItem(
                    promotion_id=promotion.id,
                    product_id=product.id,
                    qty=member.get("qty", 1),
                )
            )
        db.flush()


def seed_sets(
    db, product_by_name: dict[str, Product], brand_by_name: dict[str, Brand]
) -> None:
    for item in seed_data.SETS:
        name = item["set_name"]
        existing = db.query(Set).filter(Set.set_name == name).first()
        if existing:
            print(f"Set '{name}' already exists (id={existing.id}), skipping.")
            continue

        # Optional, unlike a product's brand - a set may legitimately have none,
        # so an absent/unknown brand leaves the set unbranded rather than
        # skipping it the way seed_products does.
        brand = brand_by_name.get(item.get("brand")) if item.get("brand") else None
        if item.get("brand") and brand is None:
            print(f"Brand '{item['brand']}' not found, leaving set '{name}' unbranded.")

        bundle = Set(
            set_name=name,
            description=item.get("description"),
            price=item["price"],
            old_price=item.get("old_price"),
            brand_id=brand.id if brand else None,
            set_image=item.get("set_image"),
            detail_image=item.get("detail_image"),
        )
        db.add(bundle)
        db.flush()
        print(f"Created set '{bundle.set_name}' (id={bundle.id}).")

        for member in item.get("items") or []:
            product = product_by_name.get(member["product"])
            if product is None:
                print(f"Product '{member['product']}' not found, skipping set item.")
                continue
            db.add(
                SetItem(
                    set_id=bundle.id,
                    product_id=product.id,
                    qty=member.get("qty", 1),
                )
            )

        # Swappable slots. A seed file exported before this feature has no
        # "option_groups" key at all, which simply leaves the set fixed.
        for position, group in enumerate(item.get("option_groups") or []):
            choices = []
            for index, choice in enumerate(group.get("choices") or []):
                product = product_by_name.get(choice["product"])
                if product is None:
                    print(
                        f"Product '{choice['product']}' not found, skipping option "
                        f"choice in '{group['name']}'."
                    )
                    continue
                choices.append(
                    SetOptionChoice(
                        product_id=product.id,
                        qty=choice.get("qty", 1),
                        price_delta=choice.get("price_delta"),
                        is_default=choice.get("is_default", False),
                        sort_order=index,
                    )
                )
            if not choices:
                continue
            if not any(c.is_default for c in choices):
                choices[0].is_default = True
            db.add(
                SetOptionGroup(
                    set_id=bundle.id,
                    name=group["name"],
                    sort_order=position,
                    choices=choices,
                )
            )
        db.flush()


def seed_users(db) -> None:
    for item in seed_data.USERS:
        existing = db.query(User).filter(User.email == item["email"]).first()
        if existing:
            print(f"User '{item['email']}' already exists (id={existing.id}), skipping.")
            continue

        user = User(
            user_name=item["user_name"],
            email=item["email"],
            hashed_password=item["hashed_password"],
            role_title=item["role_title"],
            address=item.get("address"),
            phone_num=item.get("phone_num"),
            user_image=item.get("user_image"),
            date_of_birth=_date(item.get("date_of_birth")),
            gender=item.get("gender"),
            is_active=item.get("is_active", True),
            is_verified=item.get("is_verified", True),
            user_management=item["user_management"],
            price_listing=item["price_listing"],
            product_management=item["product_management"],
            customer_management=item["customer_management"],
        )
        db.add(user)
        db.flush()
        print(f"Created user '{user.email}' (id={user.id}, role={user.role_title}).")


def seed_customers(db) -> None:
    for item in seed_data.CUSTOMERS:
        existing = db.query(Customer).filter(Customer.email == item["email"]).first()
        if existing:
            print(f"Customer '{item['email']}' already exists (id={existing.id}), skipping.")
            continue

        customer = Customer(
            customer_name=item["customer_name"],
            email=item["email"],
            hashed_password=item.get("hashed_password"),
            address=item.get("address"),
            phone_num=item.get("phone_num"),
            customer_image=item.get("customer_image"),
            date_of_birth=_date(item.get("date_of_birth")),
            gender=item.get("gender"),
            access_permission=item["access_permission"],
            is_active=item.get("is_active", True),
            is_verified=item.get("is_verified", True),
        )
        db.add(customer)
        db.flush()
        print(f"Created customer '{customer.email}' (id={customer.id}, vip={customer.access_permission}).")


def main() -> None:
    db = SessionLocal()
    try:
        brand_by_name = seed_brands(db)
        category_by_name = seed_categories(db)
        product_by_name = seed_products(db, brand_by_name, category_by_name)
        seed_product_free_items(db, product_by_name)
        seed_manuals(db, product_by_name)
        seed_promotions(db, product_by_name)
        seed_sets(db, product_by_name, brand_by_name)
        seed_users(db)
        seed_customers(db)
        db.commit()
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
