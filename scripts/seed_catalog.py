"""
Dev helper: insert a handful of sample brands/products, bypassing the API.

Usage:
    python -m scripts.seed_catalog
    (run from the project root, with the virtualenv activated and
     DATABASE_URL pointing at your Postgres instance)

Safe to re-run: brands/products are matched by name and skipped if they
already exist, so this won't create duplicates on a second run.
"""
from app.database import SessionLocal
from app.models import Brand, Product

BRANDS = ["Acme", "Globex", "Initech"]

PRODUCTS = [
    {
        "product_name": "Rocket Skates",
        "description": "Get where you're going, fast.",
        "price": "199.99",
        "old_price": "249.99",
        "brand_name": "Acme",
        "category": "Footwear",
        "badge": "New",
    },
    {
        "product_name": "Giant Rubber Band",
        "description": "Industrial strength.",
        "price": "9.99",
        "brand_name": "Acme",
        "category": "Tools",
    },
    {
        "product_name": "Standard Issue Stapler",
        "description": "Red, if you can get it.",
        "price": "24.50",
        "old_price": "29.99",
        "brand_name": "Initech",
        "category": "Office",
        "badge": "Sale",
    },
    {
        "product_name": "TPS Report Cover Sheet Pad",
        "description": "100 sheets.",
        "price": "4.99",
        "brand_name": "Initech",
        "category": "Office",
    },
    {
        "product_name": "Exploding Pen",
        "description": "Novelty item. Not responsible for damages.",
        "price": "14.00",
        "brand_name": "Globex",
        "category": "Novelty",
        "badge": "New",
    },
]


def main() -> None:
    db = SessionLocal()
    try:
        brand_by_name = {}
        for name in BRANDS:
            brand = db.query(Brand).filter(Brand.brand_name == name).first()
            if brand:
                print(f"Brand '{name}' already exists (id={brand.id}), skipping.")
            else:
                brand = Brand(brand_name=name)
                db.add(brand)
                db.flush()
                print(f"Created brand '{name}' (id={brand.id}).")
            brand_by_name[name] = brand

        for item in PRODUCTS:
            existing = db.query(Product).filter(Product.product_name == item["product_name"]).first()
            if existing:
                print(f"Product '{item['product_name']}' already exists (id={existing.id}), skipping.")
                continue

            product = Product(
                product_name=item["product_name"],
                description=item.get("description"),
                price=item["price"],
                old_price=item.get("old_price"),
                brand_id=brand_by_name[item["brand_name"]].id,
                category=item.get("category"),
                badge=item.get("badge"),
            )
            db.add(product)
            db.flush()
            print(f"Created product '{product.product_name}' (id={product.id}).")

        db.commit()
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
