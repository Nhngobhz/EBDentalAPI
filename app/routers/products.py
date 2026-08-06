from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.audit import stamp_updated_by
from app.core.bundles import build_bundle_rows, replace_bundle_rows
from app.core.deps import get_price_visibility, get_verified_user, require_permission
from app.core.files import save_image, save_named_image
from app.core.query import Limit, OptionalInt, Skip
from app.database import get_db
from app.models import Brand, Category, Product, ProductFreeItem, ProductImage, User
from app.schemas import ProductCreate, ProductOut, ProductPriceUpdate, ProductUpdate

router = APIRouter(prefix="/products", tags=["Products"])

_product_perm = Depends(require_permission("product_management"))
_price_perm = Depends(require_permission("price_listing"))

_MASKED_PRICE = "XXXX"

# Cap on how many gallery photos one POST /products/{id}/gallery may carry. Each
# is decoded and re-uploaded in-process, so an unbounded list is a slow request
# and a lot of memory - not a limit on how many a product may have in total.
MAX_GALLERY_IMAGES = 12


def _free_item_loader():
    """free_items -> the freebie Product itself, which BundleItemOut reads
    name/code/uom off (see BundleItemMixin) - without the second hop that's an
    extra query per freebie on every product listed."""
    return joinedload(Product.free_items).joinedload(ProductFreeItem.product)


def _image_loader():
    """Gallery photos, in one extra SELECT instead of a joined one: free_items is
    already joinedload'ed, and two joined collections on the same query multiply
    each other's rows (5 freebies x 6 photos = 30 rows per product)."""
    return selectinload(Product.images)


def _derive_list_price(price: Decimal, discount: Decimal, discount_type: str) -> Decimal:
    """The pre-discount price implied by `price` + the discount that produced it.

    Used ONLY when the caller doesn't send an explicit `list_price`, so existing
    integrations keep working. This is the same arithmetic the frontends used to
    run on every read - the difference is it now happens once, at write time, and
    the result is stored. Once stored it stops moving when `price` is edited,
    which is the whole point (see Product.list_price).
    """
    discount = Decimal(discount or 0)
    price = Decimal(price)
    if discount <= 0:
        return price
    if discount_type == "cash":
        return price + discount
    if discount >= 100:
        # A 100% discount can't imply an original price - there's nothing to
        # divide by. Falls back to the charged price rather than exploding.
        return price
    return (price / (Decimal(1) - discount / Decimal(100))).quantize(Decimal("0.01"))


def _resolve_list_price(
    *, list_price, price: Decimal, discount: Decimal, discount_type: str
) -> Decimal:
    """Validate an explicit list_price, or derive one when none was sent."""
    if list_price is None:
        return _derive_list_price(price, discount, discount_type)
    if Decimal(list_price) < Decimal(price):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="list_price cannot be lower than price (it is the price BEFORE discount)",
        )
    return Decimal(list_price)


def _discount_from_prices(list_price: Decimal, price: Decimal, discount_type: str) -> Decimal:
    """The discount implied by the gap between the two prices."""
    gap = Decimal(list_price) - Decimal(price)
    if gap <= 0:
        return Decimal("0")
    if discount_type == "cash":
        return gap
    return (gap / Decimal(list_price) * Decimal(100)).quantize(Decimal("0.01"))


def _sync_price_fields(product: Product, data: dict) -> None:
    """Keep price / list_price / discount consistent across a partial update.

    The rule that matters: **an existing `list_price` is never re-derived from
    `price`.** Repricing an item is a statement about what it now sells for, not
    about what it used to be worth - and re-deriving is exactly the bug this
    column was added to kill (drop price 90 -> 80 with a 10% discount attached
    and the "was" figure used to slide from 100.00 to 88.89 on its own).

    So `list_price` moves only when the caller sends one (or when a new `price`
    would otherwise exceed it, which would mean selling above list). Whatever
    that leaves, `discount` is re-derived from the gap between the two prices, so
    the "15%"/"$5.00 off" the storefront and the printed quote display always
    describes the two numbers actually being charged. An explicitly sent
    `discount` always wins over that.

    A no-op unless the payload actually touches pricing.
    """
    if not any(k in data for k in ("price", "list_price", "discount", "discount_type")):
        return

    price = Decimal(data.get("price", product.price))
    discount_type = data.get("discount_type", product.discount_type)

    list_price = data.get("list_price")
    list_price = Decimal(list_price) if list_price is not None else Decimal(product.list_price)
    if list_price < price:
        # Either an explicit list_price below the charged price (rejected - the
        # caller is confused about which is which), or a price raised above the
        # old list, which just means the item no longer sells at a discount.
        if data.get("list_price") is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="list_price cannot be lower than price (it is the price BEFORE discount)",
            )
        list_price = price

    data["list_price"] = list_price
    if data.get("discount") is None:
        data["discount"] = _discount_from_prices(list_price, price, discount_type)


def _get_product_or_404(db: Session, product_id: int) -> Product:
    product = (
        db.query(Product)
        .options(
            joinedload(Product.brand),
            joinedload(Product.category),
            _free_item_loader(),
            _image_loader(),
        )
        .filter(Product.id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


def _serialize_product(product: Product, can_view_price: bool) -> dict:
    """Only staff and customers with access_permission=True (see
    get_price_visibility) get the real price/discount. Everyone else gets
    "XXXX" in place of price, and discount left out entirely (None) -
    unauthorized viewers shouldn't learn a discount even exists."""
    data = ProductOut.model_validate(product).model_dump()
    if not can_view_price:
        data["price"] = _MASKED_PRICE
        data["discount"] = None
        # list_price is a price too. Leaving it visible would hand an unentitled
        # viewer the pre-discount figure - i.e. most of what masking `price` is
        # for - and, combined with `discount`, the exact charged price.
        data["list_price"] = None
    return data


@router.get("/", response_model=list[ProductOut])
def list_products(
    skip: Skip = 0,
    limit: Limit = 50,
    brand_id: OptionalInt = None,
    category_id: OptionalInt = None,
    q: str | None = None,
    can_view_price: bool = Depends(get_price_visibility),
    db: Session = Depends(get_db),
):
    """Public: product catalog browsing needs no account. Price/discount
    are masked unless the caller is staff or a customer with
    access_permission=True."""
    query = db.query(Product).options(
        joinedload(Product.brand),
        joinedload(Product.category),
        _free_item_loader(),
        _image_loader(),
    )
    if brand_id is not None:
        query = query.filter(Product.brand_id == brand_id)
    if category_id is not None:
        query = query.filter(Product.category_id == category_id)
    if q:
        query = query.filter(Product.product_name.ilike(f"%{q}%"))
    products = query.order_by(Product.id).offset(skip).limit(limit).all()
    return [_serialize_product(p, can_view_price) for p in products]


@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: int,
    can_view_price: bool = Depends(get_price_visibility),
    db: Session = Depends(get_db),
):
    product = _get_product_or_404(db, product_id)
    return _serialize_product(product, can_view_price)


@router.post("/", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate, current_user: User = _product_perm, db: Session = Depends(get_db)
):
    if not db.query(Brand).filter(Brand.id == payload.brand_id).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="brand_id does not exist")
    if payload.category_id is not None and not db.query(Category).filter(Category.id == payload.category_id).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="category_id does not exist")
    if payload.product_code and db.query(Product).filter(Product.product_code == payload.product_code).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="product_code already in use")

    data = payload.model_dump(exclude={"free_items"})
    data["list_price"] = _resolve_list_price(
        list_price=payload.list_price,
        price=payload.price,
        discount=payload.discount,
        discount_type=payload.discount_type,
    )
    product = Product(**data, free_items=build_bundle_rows(db, payload.free_items, ProductFreeItem))
    stamp_updated_by(product, current_user)
    db.add(product)
    db.commit()
    db.refresh(product)
    return _get_product_or_404(db, product.id)


@router.put("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    current_user: User = Depends(get_verified_user),
    db: Session = Depends(get_db),
):
    """General product update. Requires product_management for any field.
    If price/discount are included in the payload, price_listing is ALSO
    required (use PATCH /products/{id}/price if you only need to touch
    price and don't have product_management)."""
    data = payload.model_dump(exclude_unset=True)
    touches_price = any(
        k in data for k in ("price", "list_price", "discount", "discount_type")
    )

    if not current_user.product_management:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the 'product_management' permission",
        )
    if touches_price and not current_user.price_listing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Changing price/discount also requires the 'price_listing' permission",
        )

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    if "brand_id" in data and not db.query(Brand).filter(Brand.id == data["brand_id"]).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="brand_id does not exist")
    if (
        data.get("category_id") is not None
        and not db.query(Category).filter(Category.id == data["category_id"]).first()
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="category_id does not exist")
    if (
        data.get("product_code")
        and db.query(Product)
        .filter(Product.product_code == data["product_code"], Product.id != product_id)
        .first()
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="product_code already in use")

    # Free items are replaced wholesale when sent and left alone when omitted -
    # see replace_bundle_rows for why this can't just be an assignment.
    if "free_items" in data:
        replace_bundle_rows(
            db, product, "free_items", payload.free_items or [], ProductFreeItem,
            exclude_product_id=product_id,
        )
        del data["free_items"]

    _sync_price_fields(product, data)
    for field, value in data.items():
        setattr(product, field, value)
    stamp_updated_by(product, current_user)
    db.commit()
    return _get_product_or_404(db, product_id)


@router.patch("/{product_id}/price", response_model=ProductOut)
def update_product_price(
    product_id: int,
    payload: ProductPriceUpdate,
    current_user: User = _price_perm,
    db: Session = Depends(get_db),
):
    """Dedicated pricing endpoint - only needs price_listing, useful for
    a pricing-only role that lacks general product_management."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    data = payload.model_dump(exclude_unset=True)

    _sync_price_fields(product, data)
    for field, value in data.items():
        setattr(product, field, value)
    stamp_updated_by(product, current_user)
    db.commit()
    return _get_product_or_404(db, product_id)


@router.post("/{product_id}/image", response_model=ProductOut)
async def upload_product_image(
    product_id: int, file: UploadFile, current_user: User = _product_perm, db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    product.product_image = await save_named_image(file, "products", product.product_name)
    stamp_updated_by(product, current_user)
    db.commit()
    return _get_product_or_404(db, product_id)


@router.post("/{product_id}/gallery", response_model=ProductOut)
async def upload_product_gallery_images(
    product_id: int,
    files: list[UploadFile],
    current_user: User = _product_perm,
    db: Session = Depends(get_db),
):
    """Append one or more extra photos to the product's gallery (the storefront
    detail page's thumbnail strip). The primary picture is still
    POST /products/{id}/image - this never touches it.

    Uploads are APPENDED, not replaced: posting three files to a product that
    already has two leaves it with five. Remove one with the DELETE below.

    Unlike the primary image, these keep uuid filenames (save_image, not
    save_named_image) - a product-name-derived name can only identify one file,
    so a second gallery upload would overwrite the first."""
    product = _get_product_or_404(db, product_id)
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No files were uploaded"
        )
    if len(files) > MAX_GALLERY_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {MAX_GALLERY_IMAGES} images can be uploaded at once",
        )

    next_order = max((img.sort_order for img in product.images), default=-1) + 1
    for offset, file in enumerate(files):
        url = await save_image(file, "products")
        db.add(ProductImage(product_id=product_id, image=url, sort_order=next_order + offset))

    stamp_updated_by(product, current_user)
    db.commit()
    return _get_product_or_404(db, product_id)


@router.delete("/{product_id}/gallery/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product_gallery_image(
    product_id: int,
    image_id: int,
    _: User = _product_perm,
    db: Session = Depends(get_db),
):
    """Remove a single gallery photo. Scoped by product_id as well as image_id so
    an id from another product can't be deleted through this path."""
    image = (
        db.query(ProductImage)
        .filter(ProductImage.id == image_id, ProductImage.product_id == product_id)
        .first()
    )
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    db.delete(image)
    db.commit()
    return None


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, _: User = _product_perm, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    db.delete(product)  # manuals cascade-delete automatically
    db.commit()
    return None
