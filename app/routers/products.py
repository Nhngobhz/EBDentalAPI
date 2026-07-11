from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, joinedload

from app.core.deps import get_price_visibility, get_verified_user, require_permission
from app.core.files import save_image
from app.database import get_db
from app.models import Brand, Category, Product, User
from app.schemas import ProductCreate, ProductOut, ProductPriceUpdate, ProductUpdate

router = APIRouter(prefix="/products", tags=["Products"])

_product_perm = Depends(require_permission("product_management"))
_price_perm = Depends(require_permission("price_listing"))

_MASKED_PRICE = "XXXX"


def _get_product_or_404(db: Session, product_id: int) -> Product:
    product = (
        db.query(Product)
        .options(joinedload(Product.brand), joinedload(Product.category))
        .filter(Product.id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


def _serialize_product(product: Product, can_view_price: bool) -> dict:
    """Only staff and customers with access_permission=True (see
    get_price_visibility) get the real price/old_price. Everyone else gets
    "XXXX" in place of price, and old_price left out entirely (None) -
    unauthorized viewers shouldn't learn a discount even exists."""
    data = ProductOut.model_validate(product).model_dump()
    if not can_view_price:
        data["price"] = _MASKED_PRICE
        data["old_price"] = None
    return data


@router.get("/", response_model=list[ProductOut])
def list_products(
    skip: int = 0,
    limit: int = 50,
    brand_id: int | None = None,
    category_id: int | None = None,
    product_type: str | None = None,
    q: str | None = None,
    can_view_price: bool = Depends(get_price_visibility),
    db: Session = Depends(get_db),
):
    """Public: product catalog browsing needs no account. Price/old_price
    are masked unless the caller is staff or a customer with
    access_permission=True."""
    query = db.query(Product).options(joinedload(Product.brand), joinedload(Product.category))
    if brand_id is not None:
        query = query.filter(Product.brand_id == brand_id)
    if category_id is not None:
        query = query.filter(Product.category_id == category_id)
    if product_type:
        query = query.filter(Product.product_type == product_type)
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

    product = Product(**payload.model_dump())
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
    If price/old_price are included in the payload, price_listing is ALSO
    required (use PATCH /products/{id}/price if you only need to touch
    price and don't have product_management)."""
    data = payload.model_dump(exclude_unset=True)
    touches_price = "price" in data or "old_price" in data

    if not current_user.product_management:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the 'product_management' permission",
        )
    if touches_price and not current_user.price_listing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Changing price/old_price also requires the 'price_listing' permission",
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
    for field, value in payload.model_dump(exclude_unset=True).items():
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
    product.product_image = await save_image(file, "products")
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
