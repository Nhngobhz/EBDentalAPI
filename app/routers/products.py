from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, joinedload

from app.core.bundles import build_bundle_rows, replace_bundle_rows
from app.core.deps import get_price_visibility, get_verified_user, require_permission
from app.core.files import save_named_image
from app.core.query import OptionalInt
from app.database import get_db
from app.models import Brand, Category, Product, ProductFreeItem, User
from app.schemas import ProductCreate, ProductOut, ProductPriceUpdate, ProductUpdate

router = APIRouter(prefix="/products", tags=["Products"])

_product_perm = Depends(require_permission("product_management"))
_price_perm = Depends(require_permission("price_listing"))

_MASKED_PRICE = "XXXX"


def _free_item_loader():
    """free_items -> the freebie Product itself, which BundleItemOut reads
    name/code/uom off (see BundleItemMixin) - without the second hop that's an
    extra query per freebie on every product listed."""
    return joinedload(Product.free_items).joinedload(ProductFreeItem.product)


def _get_product_or_404(db: Session, product_id: int) -> Product:
    product = (
        db.query(Product)
        .options(joinedload(Product.brand), joinedload(Product.category), _free_item_loader())
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
    return data


@router.get("/", response_model=list[ProductOut])
def list_products(
    skip: int = 0,
    limit: int = 50,
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
        joinedload(Product.brand), joinedload(Product.category), _free_item_loader()
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
    payload: ProductCreate, _: User = _product_perm, db: Session = Depends(get_db)
):
    if not db.query(Brand).filter(Brand.id == payload.brand_id).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="brand_id does not exist")
    if payload.category_id is not None and not db.query(Category).filter(Category.id == payload.category_id).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="category_id does not exist")
    if payload.product_code and db.query(Product).filter(Product.product_code == payload.product_code).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="product_code already in use")

    data = payload.model_dump(exclude={"free_items"})
    product = Product(**data, free_items=build_bundle_rows(db, payload.free_items, ProductFreeItem))
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
    touches_price = "price" in data or "discount" in data or "discount_type" in data

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

    for field, value in data.items():
        setattr(product, field, value)
    db.commit()
    return _get_product_or_404(db, product_id)


@router.patch("/{product_id}/price", response_model=ProductOut)
def update_product_price(
    product_id: int,
    payload: ProductPriceUpdate,
    _: User = _price_perm,
    db: Session = Depends(get_db),
):
    """Dedicated pricing endpoint - only needs price_listing, useful for
    a pricing-only role that lacks general product_management."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    data = payload.model_dump(exclude_unset=True)

    for field, value in data.items():
        setattr(product, field, value)
    db.commit()
    return _get_product_or_404(db, product_id)


@router.post("/{product_id}/image", response_model=ProductOut)
async def upload_product_image(
    product_id: int, file: UploadFile, _: User = _product_perm, db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    product.product_image = await save_named_image(file, "products", product.product_name)
    db.commit()
    return _get_product_or_404(db, product_id)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, _: User = _product_perm, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    db.delete(product)  # manuals cascade-delete automatically
    db.commit()
    return None
