from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import func, nullslast, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.audit import stamp_updated_by
from app.core.bundles import build_bundle_rows, replace_bundle_rows
from app.core.deps import get_price_visibility, get_verified_user, require_permission
from app.core.files import save_image, save_named_image
from app.core.query import Limit, OptionalInt, OptionalIntList, Skip
from app.database import get_db
from app.models import Brand, Category, Product, ProductFreeItem, ProductImage, User
from app.schemas import (
    ProductCount,
    ProductCreate,
    ProductFacets,
    ProductOut,
    ProductPriceUpdate,
    ProductSort,
    ProductUpdate,
    SectionFilter,
)

router = APIRouter(prefix="/products", tags=["Products"])

_product_perm = Depends(require_permission("product_management"))
_price_perm = Depends(require_permission("price_listing"))

_MASKED_PRICE = "XXXX"

# Cap on how many gallery photos one POST /products/{id}/gallery may carry. Each
# is decoded and re-uploaded in-process, so an unbounded list is a slow request
# and a lot of memory - not a limit on how many a product may have in total.
MAX_GALLERY_IMAGES = 12

# Where an uploaded product photo is filed. Machinery and materials are two
# catalogues with two different lifecycles - machinery pictures are shot in-house and
# live as long as the product does, while materials arrive from a SAP item master
# that is re-imported nightly and can withdraw thousands of rows at a time - so their
# images are kept apart rather than in one flat products/ folder. That makes it
# possible to sync, back up, purge or hand off one shop's media without touching the
# other's, which a single folder of 8,000 mixed files makes impossible.
#
# Existing images are NOT moved: `product_image` stores the full path/URL that was
# written at upload time, so old files keep resolving from products/ and only new
# uploads land in the new place. A rename pass would have to rewrite every stored
# URL to gain nothing a reader can see.
def _image_folder(product: Product) -> str:
    return f"products/{product.section}"


# What each `sort` value means, as an explicit ORDER BY. A closed map rather than
# anything built from the caller's string - see schemas.ProductSort.
#
# Every entry ends in Product.id. Ties are the norm here (a whole category at one
# price, 7,000 materials with a NULL stock figure), and without a deterministic last
# key the database is free to order tied rows differently between requests - which,
# on a paged endpoint, silently drops some rows and repeats others.
#
# nullslast() on the stock sorts for the same reason: stock_qty is NULL for every
# machinery product (they never enter SAP), and Postgres sorts NULLs first on DESC,
# so "most stock first" would otherwise open with every item whose stock is unknown.
_SORTS = {
    "name": lambda: (func.lower(Product.product_name).asc(), Product.id.asc()),
    "name_desc": lambda: (func.lower(Product.product_name).desc(), Product.id.asc()),
    "price_asc": lambda: (Product.price.asc(), Product.id.asc()),
    "price_desc": lambda: (Product.price.desc(), Product.id.asc()),
    "stock_asc": lambda: (nullslast(Product.stock_qty.asc()), Product.id.asc()),
    "stock_desc": lambda: (nullslast(Product.stock_qty.desc()), Product.id.asc()),
    "newest": lambda: (Product.created_at.desc(), Product.id.desc()),
    "oldest": lambda: (Product.created_at.asc(), Product.id.asc()),
}

# Orderings that are the price list in another form. A caller whose prices are
# masked (see get_price_visibility) is refused these: every figure is withheld
# from the card, but "cheapest first" still ranks all 8,125 items by it, and a
# ranking read together with any one known price brackets every neighbour.
#
# A silent fall back to the default rather than a 422, matching what the endpoint
# already does with the rest of a request it cannot honour as asked - the caller
# gets the catalogue, just not in that order. The storefront hides the Sort
# control from these shoppers entirely (sap_catalog.can_sort); this is the half
# that holds when the parameter is typed into the URL instead.
_PRICE_SORTS = {"price_asc", "price_desc"}


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


def _catalog_query(
    db: Session,
    *,
    brand_id: int | None,
    category_id: int | list[int] | None,
    q: str | None,
    section: str,
    include_unpurchasable: bool,
    include_delisted: bool = False,
):
    """The catalog's filter set, without ordering, paging or eager loads.

    Shared by GET /products/ and GET /products/count so the two cannot disagree.
    They must agree exactly: the count is what a paginated storefront divides into
    page numbers, so a filter applied to one and not the other produces pages that
    are empty at the end, or a last page nobody can reach.
    """
    query = db.query(Product)
    if not include_unpurchasable:
        query = query.filter(Product.is_purchasable.is_(True))
    # Products SAP has withdrawn are hidden from every public read by default, and
    # the default is what matters: a caller that forgets to ask gets the safe
    # answer. The admin table passes include_delisted so staff can still find one -
    # it is still a real row, still on past orders, and still editable.
    if not include_delisted:
        query = query.filter(Product.delisted_at.is_(None))
    if section != "all":
        query = query.filter(Product.section == section)
    if brand_id is not None:
        query = query.filter(Product.brand_id == brand_id)
    if category_id is not None:
        # One category or several. The materials catalog's filter panel is a list
        # of checkboxes, so it asks for "Diamond Burs OR Endo Files" as a repeated
        # parameter; every other caller passes a bare int and lands in the same
        # place through a one-element list. An EMPTY list is not a filter - it is
        # what an untouched panel sends - and must not be turned into
        # `IN ()`, which matches nothing and would empty the grid.
        wanted = category_id if isinstance(category_id, list) else [category_id]
        if wanted:
            query = query.filter(Product.category_id.in_(wanted))
    if q:
        # Name OR code. Code matters as much as name in the materials half, where
        # the catalogue is 8,000 SAP items and staff know things by their item code
        # ("(AL)28A1E2") far more reliably than by a name that starts with the same
        # three words as forty others.
        needle = f"%{q}%"
        query = query.filter(
            or_(Product.product_name.ilike(needle), Product.product_code.ilike(needle))
        )
    return query


@router.get("/", response_model=list[ProductOut])
def list_products(
    skip: Skip = 0,
    limit: Limit = 50,
    brand_id: OptionalInt = None,
    category_id: OptionalIntList = None,
    q: str | None = None,
    section: SectionFilter = "machinery",
    sort: ProductSort = "name",
    include_unpurchasable: bool = False,
    include_delisted: bool = False,
    can_view_price: bool = Depends(get_price_visibility),
    db: Session = Depends(get_db),
):
    """Public: product catalog browsing needs no account. Price/discount
    are masked unless the caller is staff or a customer with
    access_permission=True - and a masked caller asking for a price ordering gets
    the default one instead (see _PRICE_SORTS).

    Gift-only products (is_purchasable=False) are left out by default - they
    can't be bought, so a storefront listing that offers them is a dead end.
    `include_unpurchasable=true` brings them back for the screens that must see
    every row: the admin product table and the free-item picker that builds
    bundles out of them.

    `section` defaults to "machinery" rather than to "everything", and that default
    is the point: materials come from SAP and must never turn up on a machinery
    page, so a caller that forgets to ask gets the same rows it got before the
    column existed. Pass "all" from the screens that genuinely span both.

    `category_id` may be repeated - "?category_id=8&category_id=19" reads as "in
    either" - because the materials catalog filters with a panel of checkboxes and
    is paged on the server: with 8,000 items it holds 24 rows at a time and cannot
    do the OR itself the way the machinery page does (see blueprints/catalog.py).
    """
    query = _catalog_query(
        db,
        brand_id=brand_id,
        category_id=category_id,
        q=q,
        section=section,
        include_unpurchasable=include_unpurchasable,
        include_delisted=include_delisted,
    ).options(
        joinedload(Product.brand),
        joinedload(Product.category),
        _free_item_loader(),
        _image_loader(),
    )
    # Alphabetical by default, not insertion order. This is a *catalog*: a shopper
    # scanning it for "Curing Light" needs the C's together, and staff paging through
    # the admin table need the same row to stay in the same place instead of drifting
    # to the end every time a product is re-created. Brand/Category already sort by
    # name.
    #
    # It has to be the DATABASE that sorts, not the page: `limit` slices the result,
    # so sorting client-side would only ever order whichever 50 rows happened to come
    # back first. That is exactly why `sort` is a parameter here rather than something
    # the materials catalog does to the 24 cards it was handed - "cheapest first"
    # across 8,125 items is a different query, not a different rendering.
    if sort in _PRICE_SORTS and not can_view_price:
        sort = "name"
    products = (
        query.order_by(*_SORTS[sort]())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_serialize_product(p, can_view_price) for p in products]


# Declared BEFORE /{product_id}: FastAPI matches routes in declaration order, and
# the other way round "count" is offered to that route as an int and 422s.
@router.get("/count", response_model=ProductCount)
def count_products(
    brand_id: OptionalInt = None,
    category_id: OptionalIntList = None,
    q: str | None = None,
    section: SectionFilter = "machinery",
    include_unpurchasable: bool = False,
    include_delisted: bool = False,
    db: Session = Depends(get_db),
):
    """How many products match, ignoring paging.

    Exists for the materials catalog. Machinery is ~110 products and fits in one
    `limit=500` fetch, so the page could count what it already had; materials is
    8,000+ and has to be paged server-side, at which point the page holds 24 rows
    and genuinely cannot know whether there are three more or three thousand.

    Takes no price-visibility dependency: a count is not a price, and it is the
    same number for every viewer.

    `category_id` repeats here exactly as it does on GET /products/ - it has to,
    since this is the number that listing is divided into pages by.
    """
    total = _catalog_query(
        db,
        brand_id=brand_id,
        category_id=category_id,
        q=q,
        section=section,
        include_unpurchasable=include_unpurchasable,
        include_delisted=include_delisted,
    ).count()
    return {"count": total}


@router.get("/facets", response_model=ProductFacets)
def product_facets(
    brand_id: OptionalInt = None,
    category_id: OptionalIntList = None,
    q: str | None = None,
    section: SectionFilter = "machinery",
    include_unpurchasable: bool = False,
    include_delisted: bool = False,
    db: Session = Depends(get_db),
):
    """Which categories and brands the matching products fall into, with counts.

    Also for the materials catalog, and for the same reason /count exists: at 8,000
    items over 824 categories the storefront has to lead with the groups rather
    than the items, and a group is only worth offering if you can say how big it
    is. GET /categories/ can't answer that - it lists every category in the
    database, machinery's included, with no idea how many materials sit behind
    each - so a dropdown built from it offers 854 options, thirty of which lead to
    an empty grid.

    Counted over the *filtered* set, so the numbers describe what clicking would
    actually yield: pick a brand and the category list shrinks to that brand's
    categories, with that brand's counts.

    Like /count, this takes no price-visibility dependency - a count is not a
    price.
    """
    def buckets(group_column, entity, name_column, extra_column=None, extra_key=None, **drop):
        # `drop` blanks the grouped column's OWN filter. Counting categories with
        # category_id still applied would return a bucket only for the categories
        # already ticked, leaving the page no way to offer the others without
        # clearing the filter first - and on a multi-select panel that is the one
        # thing the counts are for, since ticking a second box is the normal next
        # move. Every other filter stays, which is what makes the brand counts on a
        # category page describe that category.
        base = _catalog_query(
            db,
            **{
                "brand_id": brand_id,
                "category_id": category_id,
                "q": q,
                "section": section,
                "include_unpurchasable": include_unpurchasable,
                "include_delisted": include_delisted,
                **drop,
            },
        )
        # One optional column each side, under a different key: a brand bucket
        # carries its logo, a category bucket the admin's icon override. Selected in
        # the GROUP BY rather than fetched per bucket afterwards - 824 categories is
        # 824 extra queries otherwise.
        columns = [entity.id, name_column]
        if extra_column is not None:
            columns.append(extra_column)
        rows = (
            base.with_entities(*columns, func.count(Product.id).label("n"))
            .join(entity, group_column == entity.id)
            .group_by(*columns)
            .order_by(func.count(Product.id).desc(), name_column)
            .all()
        )
        return [
            {
                "id": row[0],
                "name": row[1],
                "count": row[-1],
                **({extra_key: row[2]} if extra_column is not None else {}),
            }
            for row in rows
        ]

    return {
        "categories": buckets(
            Product.category_id,
            Category,
            Category.category_name,
            Category.category_icon,
            "icon",
            category_id=None,
        ),
        "brands": buckets(
            Product.brand_id,
            Brand,
            Brand.brand_name,
            Brand.brand_image,
            "image",
            brand_id=None,
        ),
    }


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
    # see replace_bundle_rows, which reconciles rather than re-creating.
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
    # Filed under products/machinery or products/materials - see _image_folder.
    product.product_image = await save_named_image(
        file, _image_folder(product), product.product_name
    )
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
        url = await save_image(file, _image_folder(product))
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
