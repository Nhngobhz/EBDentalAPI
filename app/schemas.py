"""
Pydantic schemas used for request validation and response serialization.

Naming convention used throughout:
  *Create        -> payload to create a new record
  *Update        -> payload to update an existing record (all fields optional)
  *Out           -> what gets returned to the client
"""
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# Shared / auth
# ---------------------------------------------------------------------------
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class Message(BaseModel):
    detail: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=72)


# --- Optional demographics, shared by User and Customer --------------------
# Both principal types carry the same pair of columns; these two aliases are
# what keeps the validation identical on all six payload schemas below.
Gender = Literal["male", "female", "other"]


def _validate_date_of_birth(value: Optional[date]) -> Optional[date]:
    """Both bounds exist only to catch typos (a mistyped year is the common
    one) - the field is optional and clearing it back to null is always
    allowed."""
    if value is None:
        return value
    if value > date.today():
        raise ValueError("Date of birth cannot be in the future")
    if value.year < 1900:
        raise ValueError("Date of birth must be on or after 1900-01-01")
    return value


# Attached via Annotated rather than a @field_validator repeated in each class.
DateOfBirth = Annotated[Optional[date], AfterValidator(_validate_date_of_birth)]


# --- Optional map location, shared by Customer and Order -------------------
# A delivery point, stored three ways because the customer can supply it two
# ways (drop a pin, or paste a Google Maps link) and neither always yields the
# other - see the column comments on Customer.latitude in models.py.
#
# Latitude/longitude are Decimal, not float: they land in Numeric(9, 6) columns
# and go back out as strings like every other Decimal in this file, so a
# round-trip never picks up binary-float drift in the sixth decimal place. The
# bounds are the real ones - anything outside them is a transposed pair or a
# mis-parsed link, not a place on Earth.
#
# Like DateOfBirth above, these carry no default: each field spells out its own
# `= None`, which is what lets an explicit null mean "clear the pin".
Latitude = Annotated[Optional[Decimal], Field(ge=-90, le=90)]
Longitude = Annotated[Optional[Decimal], Field(ge=-180, le=180)]


# Hosts a map link may point at. Narrow on purpose, and the reason is worth
# spelling out: unlike every other free-text URL in this schema (HeroSlide's
# button_url, say) this one is written by CUSTOMERS and read by STAFF, who see
# it rendered as an "Open in Google Maps" link on the admin Customers table and
# on an order. An unrestricted field there is a way for anyone with an account
# to put a link of their choosing, under a trustworthy label, in front of the
# people who run the shop. Refusing a scheme that executes is not enough for
# that - https://evil.example/ needs no scheme trick at all.
#
# Everything a real paste produces is in here: the Google Maps site on any
# country domain, both Google shorteners, and OpenStreetMap.
_MAP_LINK_HOST_RE = re.compile(
    r"^(?:[a-z0-9-]+\.)*(?:google\.[a-z.]{2,7}|goo\.gl|openstreetmap\.org|osm\.org)$"
)


def _validate_map_link(value: Optional[str]) -> Optional[str]:
    """A map link, or a clear refusal. Two gates, for two different attacks:

    1. **Scheme.** `javascript:`/`data:` in an href is stored XSS the moment a
       page renders it, and escaping does not help - those payloads need no
       special characters. Same reasoning as resolve_link_url() on the Flask
       side, which re-checks at render time.
    2. **Host.** See _MAP_LINK_HOST_RE above - this field is customer-written
       and staff-clicked, so "is a URL" is not a high enough bar.

    Refusing outright rather than silently dropping the value: someone pasting
    a link from an app we do not know needs to be told, not left believing
    their location was saved."""
    if value is None:
        return value
    value = value.strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Map link must be a http:// or https:// URL")
    if not _MAP_LINK_HOST_RE.match((parsed.hostname or "").lower()):
        raise ValueError(
            "Map link must be a Google Maps or OpenStreetMap link "
            "(for anything else, drop a pin on the map instead)"
        )
    return value


MapLink = Annotated[Optional[str], Field(max_length=500), AfterValidator(_validate_map_link)]


# ---------------------------------------------------------------------------
# User (staff / admin accounts)
# ---------------------------------------------------------------------------
class UserBase(BaseModel):
    user_name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    address: Optional[str] = Field(None, max_length=255)
    phone_num: Optional[str] = Field(None, max_length=30)
    date_of_birth: DateOfBirth = None
    gender: Optional[Gender] = None


class UserCreateByAdmin(UserBase):
    """Used by an existing admin (user_management=True) to create a new
    staff account with a role/permissions already assigned."""

    password: str = Field(..., min_length=8, max_length=72)
    role_title: str = Field("Staff", max_length=100)
    user_management: bool = False
    price_listing: bool = False
    product_management: bool = False
    customer_management: bool = False
    # Site-wide configuration (the Settings screen). See app/core/deps.py - not
    # implied by the four above.
    admin: bool = False


class UserUpdateSelf(BaseModel):
    user_name: Optional[str] = Field(None, min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    address: Optional[str] = Field(None, max_length=255)
    phone_num: Optional[str] = Field(None, max_length=30)
    date_of_birth: DateOfBirth = None
    gender: Optional[Gender] = None


class ChangePassword(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=72)
    new_password: str = Field(..., min_length=8, max_length=72)


class UserAdminSetPassword(BaseModel):
    """Used by a user_management admin to directly set another staff member's password -
    unlike ChangePassword (self-service), there's no current_password check since the
    caller isn't the account owner. See PUT /users/{id}/password."""

    new_password: str = Field(..., min_length=8, max_length=72)


class UserUpdateByAdmin(BaseModel):
    user_name: Optional[str] = Field(None, min_length=2, max_length=100)
    address: Optional[str] = Field(None, max_length=255)
    phone_num: Optional[str] = Field(None, max_length=30)
    date_of_birth: DateOfBirth = None
    gender: Optional[Gender] = None
    role_title: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None
    user_management: Optional[bool] = None
    price_listing: Optional[bool] = None
    product_management: Optional[bool] = None
    customer_management: Optional[bool] = None
    admin: Optional[bool] = None


class UserOut(UserBase):
    id: int
    user_image: Optional[str] = None
    role_title: str
    creation_date: datetime
    is_active: bool
    is_verified: bool
    user_management: bool
    price_listing: bool
    product_management: bool
    customer_management: bool
    admin: bool
    updated_at: datetime
    updated_by: Optional["UserMini"] = None

    model_config = ConfigDict(from_attributes=True)


class UserMini(BaseModel):
    """Just enough of a `User` to name them. Used for the `updated_by` on every
    audited row - the id alone would make an admin screen say "edited by 3", and
    the full UserOut would leak a staff member's address/phone/permissions into
    the response of anything they once touched (including public product reads)."""

    id: int
    user_name: str

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------
class CustomerBase(BaseModel):
    customer_name: str = Field(..., min_length=2, max_length=150)
    email: EmailStr
    address: Optional[str] = Field(None, max_length=255)
    phone_num: Optional[str] = Field(None, max_length=30)
    date_of_birth: DateOfBirth = None
    gender: Optional[Gender] = None
    # Optional delivery pin - see the Latitude/Longitude/MapLink aliases above.
    latitude: Latitude = None
    longitude: Longitude = None
    map_link: MapLink = None


class CustomerCreate(CustomerBase):
    """Used by staff (customer_management) to create a plain customer
    record. No password - this customer cannot log in themselves unless
    they separately self-register via POST /auth/customer/register."""

    access_permission: bool = False


class CustomerUpdate(BaseModel):
    customer_name: Optional[str] = Field(None, min_length=2, max_length=150)
    email: Optional[EmailStr] = None
    address: Optional[str] = Field(None, max_length=255)
    phone_num: Optional[str] = Field(None, max_length=30)
    date_of_birth: DateOfBirth = None
    gender: Optional[Gender] = None
    # Optional delivery pin - see the Latitude/Longitude/MapLink aliases above.
    latitude: Latitude = None
    longitude: Longitude = None
    map_link: MapLink = None
    access_permission: Optional[bool] = None


class CustomerOut(CustomerBase):
    id: int
    customer_image: Optional[str] = None
    access_permission: bool
    is_active: bool
    is_verified: bool
    creation_date: datetime
    updated_at: datetime
    updated_by: Optional[UserMini] = None

    model_config = ConfigDict(from_attributes=True)


class CustomerRegister(CustomerBase):
    """Public self-registration. New customers start with
    access_permission=False - a customer_management staff member must
    grant it (see PUT /customers/{id}) before prices become visible.
    Email confirmation is required before login."""

    password: str = Field(..., min_length=8, max_length=72)


class CustomerSelfUpdate(BaseModel):
    customer_name: Optional[str] = Field(None, min_length=2, max_length=150)
    email: Optional[EmailStr] = None
    address: Optional[str] = Field(None, max_length=255)
    phone_num: Optional[str] = Field(None, max_length=30)
    date_of_birth: DateOfBirth = None
    gender: Optional[Gender] = None
    # Optional delivery pin - see the Latitude/Longitude/MapLink aliases above.
    latitude: Latitude = None
    longitude: Longitude = None
    map_link: MapLink = None


class CustomerLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    customer: CustomerOut


class GoogleAuthRequest(BaseModel):
    """Body of POST /auth/google. `credential` is the ID token Google
    Identity Services handed the browser - see app/core/google_auth.py."""

    credential: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    """Response for POST /auth/login, which accepts both staff and customer
    credentials (tries a User match first, then falls back to Customer).
    `account_type` tells the caller which one logged in - only the
    matching one of `user` / `customer` is populated."""

    access_token: str
    token_type: str = "bearer"
    account_type: Literal["user", "customer"]
    user: Optional[UserOut] = None
    customer: Optional[CustomerOut] = None


# ---------------------------------------------------------------------------
# Brand
# ---------------------------------------------------------------------------
class BrandBase(BaseModel):
    brand_name: str = Field(..., min_length=1, max_length=150)


class BrandUpdate(BaseModel):
    brand_name: Optional[str] = Field(None, min_length=1, max_length=150)


class BrandOut(BrandBase):
    id: int
    brand_image: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    updated_by: Optional[UserMini] = None

    model_config = ConfigDict(from_attributes=True)


class BrandMini(BaseModel):
    """Small nested representation used inside ProductOut."""

    id: int
    brand_name: str
    brand_image: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------
class CategoryBase(BaseModel):
    category_name: str = Field(..., min_length=1, max_length=150)
    # A Font Awesome class ("fa-teeth"), overriding the storefront's name-based
    # guess. Null means "guess" - see models.Category.category_icon. Replaces the
    # dropped category_image.
    category_icon: Optional[str] = Field(None, max_length=60)


class CategoryUpdate(BaseModel):
    category_name: Optional[str] = Field(None, min_length=1, max_length=150)
    # Sending null clears the override and puts the category back on the guess,
    # which is how the admin form erases a chosen icon.
    category_icon: Optional[str] = Field(None, max_length=60)


class CategoryOut(CategoryBase):
    id: int
    created_at: datetime
    updated_at: datetime
    updated_by: Optional[UserMini] = None

    model_config = ConfigDict(from_attributes=True)


class CategoryMini(BaseModel):
    """Small nested representation used inside ProductOut."""

    id: int
    category_name: str
    category_icon: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------
# Shared by Product and Order - either a percentage (0-100) or a flat $ amount off,
# depending on context. Declared once here (rather than separately near OrderCreate)
# since ProductBase needs it first in file order.
DiscountType = Literal["percent", "cash"]

# Which half of the storefront a product belongs to - see models.Product.section.
# Narrower than QrBadgeVariant below, which also carries "implants": that one labels
# a contact-page card, while this one decides which catalog a product appears in,
# and there are exactly two catalogs.
Section = Literal["machinery", "materials"]

# What GET /products/ accepts. Deliberately NOT `Optional[Section] = None` meaning
# "every section": the default has to be the SAFE answer, because the failure mode
# this whole column exists to prevent is materials silently appearing on a machinery
# page. A caller that says nothing gets machinery - exactly what it got before this
# column existed - and "all" is a deliberate opt-in for the screens that must see
# every row, the same way include_unpurchasable already works on that endpoint.
SectionFilter = Literal["machinery", "materials", "all"]

# How GET /products/ orders what it returns.
#
# A closed vocabulary rather than a free-text "order_by" column name, because the
# value arrives from a query string: anything that lets a caller name a column is a
# way to sort by a column the endpoint never meant to expose, and (with the wrong
# implementation) a way to inject SQL. The router maps each of these to an explicit
# ORDER BY - see _SORTS there.
#
# "name" stays the default, which is what the endpoint did before this parameter
# existed. "stock" is only meaningful for materials (machinery never enters SAP, so
# its stock_qty is NULL throughout) but is accepted for either, and the router puts
# the NULLs last so an unsynced product never occupies the top of a stock sort.
ProductSort = Literal[
    "name",
    "name_desc",
    "price_asc",
    "price_desc",
    "stock_asc",
    "stock_desc",
    "newest",
    "oldest",
]


# ---------------------------------------------------------------------------
# Bundle contents - the shared "this thing contains these products" shape used
# by Promotion.items, Set.items and Product.free_items (see BundleItemMixin in
# app/models.py). Only product_id + qty are ever accepted; everything shown back
# is read from the live Product row, so renaming a product updates every bundle
# it appears in.
# ---------------------------------------------------------------------------
# Upper bound on any client-supplied quantity. Every money column in the schema is
# Numeric(10, 2), i.e. it tops out just under $100,000,000 - so an unbounded qty
# turned "price x qty" into a Postgres numeric-overflow error, which surfaced as an
# opaque 500 (and a Telegram error alert) rather than a validation message. 100,000
# is far beyond any real dental order and far below the overflow point.
MAX_QTY = 100_000


class BundleItemIn(BaseModel):
    product_id: int
    qty: int = Field(1, gt=0, le=MAX_QTY)


class BundleItemOut(BaseModel):
    product_id: int
    product_name: str
    product_code: Optional[str] = None
    uom: Optional[str] = None
    qty: int

    model_config = ConfigDict(from_attributes=True)


class ProductImageOut(BaseModel):
    """An extra gallery photo. `image` is a full URL / store-api-relative path,
    same as every other *_image field (see app/core/files.py)."""

    id: int
    image: str
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class ProductBase(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    badge: Optional[str] = Field(None, max_length=50)
    product_code: Optional[str] = Field(None, max_length=50)
    uom: Optional[str] = Field(None, max_length=20)
    # Defaults to "machinery" so every existing caller - the admin product form,
    # the seed scripts, the test suite - keeps creating machinery products without
    # being changed. Only the SAP item sync ever sends "materials".
    section: Section = "machinery"
    # False = gift-only: expands as a $0 component line under whatever it comes
    # with, but can't be ordered on its own and isn't listed in the public
    # catalog. Defaults to True so every existing caller keeps creating sellable
    # products - see models.Product.is_purchasable.
    is_purchasable: bool = True


class ProductCreate(ProductBase):
    price: Decimal = Field(..., gt=0)
    # The pre-discount price. Optional: omit it and the router derives one from
    # price+discount (so existing callers keep working unchanged), send it and it's
    # stored verbatim. Must not be below `price` - see _resolve_list_price.
    list_price: Optional[Decimal] = Field(None, gt=0)
    discount_type: DiscountType = "percent"
    # Decimal("0") not bare 0 - Pydantic v2 doesn't validate/coerce field defaults unless
    # asked to, so a bare int default would reach the DB as a Python int on a Numeric
    # column (harmless in Postgres, but produces a serializer warning on the way back out).
    discount: Decimal = Field(Decimal("0"), ge=0)
    brand_id: int
    category_id: Optional[int] = None
    # Products this one comes with for free. Each becomes a $0 line on any order
    # containing this product - see routers/orders.py::create_order.
    free_items: list[BundleItemIn] = []

    @field_validator("discount")
    @classmethod
    def _percent_within_100(cls, discount, info):
        if info.data.get("discount_type") == "percent" and discount > 100:
            raise ValueError("A percent discount cannot exceed 100")
        return discount


class ProductUpdate(BaseModel):
    product_name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    badge: Optional[str] = Field(None, max_length=50)
    product_code: Optional[str] = Field(None, max_length=50)
    uom: Optional[str] = Field(None, max_length=20)
    section: Optional[Section] = None
    is_purchasable: Optional[bool] = None
    brand_id: Optional[int] = None
    category_id: Optional[int] = None
    # Present here so a product_management holder *can* still create a
    # product with a price, but changing price/discount on an *existing*
    # product additionally requires the price_listing permission - enforced
    # in the router, not here.
    price: Optional[Decimal] = Field(None, gt=0)
    list_price: Optional[Decimal] = Field(None, gt=0)
    discount_type: Optional[DiscountType] = None
    discount: Optional[Decimal] = Field(None, ge=0)
    # Omitted -> the product's existing free items are left alone; sent (even as
    # []) -> they're replaced wholesale by what's sent. Same rule as
    # PromotionUpdate.items/SetUpdate.items.
    free_items: Optional[list[BundleItemIn]] = None

    @field_validator("discount")
    @classmethod
    def _percent_within_100(cls, discount, info):
        if discount is not None and info.data.get("discount_type") == "percent" and discount > 100:
            raise ValueError("A percent discount cannot exceed 100")
        return discount


class ProductPriceUpdate(BaseModel):
    price: Optional[Decimal] = Field(None, gt=0)
    list_price: Optional[Decimal] = Field(None, gt=0)
    discount_type: Optional[DiscountType] = None
    discount: Optional[Decimal] = Field(None, ge=0)

    @field_validator("discount")
    @classmethod
    def _percent_within_100(cls, discount, info):
        if discount is not None and info.data.get("discount_type") == "percent" and discount > 100:
            raise ValueError("A percent discount cannot exceed 100")
        return discount


class ProductCount(BaseModel):
    """How many products match a filter. An object rather than a bare integer so
    the endpoint can gain a sibling figure later without breaking its callers."""

    count: int


class FacetBucket(BaseModel):
    """One row of a facet count: a brand or category, and how many products in the
    current filter set belong to it."""

    id: int
    name: str
    count: int
    # Only ever set on a brand bucket, so the storefront can show a logo wall
    # instead of a list of names. None on categories, which no longer have an image
    # column at all - they carry a glyph instead, in `icon` below.
    image: Optional[str] = None
    # The mirror of `image` on the other side: only ever set on a category bucket,
    # and usually null even there. It is the admin's OVERRIDE of the storefront's
    # name-based guess (see models.Category.category_icon), so it has to travel with
    # the bucket - the facet list is the only thing the categories index, the home
    # page tiles and the filter rail are built from, and without it those three
    # screens would keep guessing while the admin panel showed a chosen icon.
    icon: Optional[str] = None


class ProductFacets(BaseModel):
    """Every category and brand present in a filter set, with counts.

    Exists for the materials storefront's category-first browsing. Materials is
    8,000+ SAP items spread over 824 categories and 173 brands, and the only way
    to offer that as something a person can browse is to lead with the groups
    rather than the items - which means knowing how many items each group holds.
    Asking GET /products/count once per category would be 824 requests; this is
    one GROUP BY.

    Buckets are ordered by count descending, then name, so a caller that wants
    "the twelve biggest categories" can take the first twelve without sorting.
    """

    categories: list[FacetBucket]
    brands: list[FacetBucket]


class ProductOut(ProductBase):
    id: int
    # Union[Decimal, str]: viewers without price access (see
    # app.core.deps.get_price_visibility) get back the literal string
    # "XXXX" instead of the real value.
    price: Union[Decimal, str]
    discount_type: DiscountType = "percent"
    # Masked to None for the same viewers, same reasoning as price - see
    # app.routers.products._serialize_product. list_price is masked too: it IS a
    # price, so returning it to an unentitled viewer would hand back most of what
    # masking `price` is meant to withhold.
    discount: Optional[Decimal] = None
    list_price: Optional[Decimal] = None
    product_image: Optional[str] = None
    # On-hand quantity from the last SAP sync, and when that sync ran. Read-only:
    # they appear here but deliberately NOT in ProductCreate/ProductUpdate, because
    # scripts/sap_sync.py owns them. Letting the admin form send a stock figure
    # would produce a value that edits cleanly, saves cleanly, and is overwritten
    # without warning by the next sync - see models.Product.stock_qty.
    #
    # NULL means "never synced" (all machinery), not "none in stock".
    stock_qty: Optional[Decimal] = None
    stock_synced_at: Optional[datetime] = None
    # Set when SAP stopped listing the item; NULL means currently listed. Read-only
    # here for the same reason the stock fields are - scripts/sap_sync.py owns it,
    # and a value an admin could set by hand would be reverted by the next sync.
    # Public catalog reads never return a delisted product at all, so in practice
    # this is only ever non-NULL for a caller that passed include_delisted.
    delisted_at: Optional[datetime] = None
    # Additional photos for the storefront gallery. `product_image` above is
    # still the primary one and is NOT repeated here - see models.ProductImage.
    images: list[ProductImageOut] = []
    brand: Optional[BrandMini] = None
    category: Optional[CategoryMini] = None
    free_items: list[BundleItemOut] = []
    created_at: datetime
    updated_at: datetime
    updated_by: Optional[UserMini] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Manual
# ---------------------------------------------------------------------------
class ManualBase(BaseModel):
    # What this document is called. Optional - see Manual.title.
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None


class ManualUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    product_id: Optional[int] = None


class ProductMini(BaseModel):
    id: int
    product_name: str

    model_config = ConfigDict(from_attributes=True)


class ManualOut(ManualBase):
    id: int
    manual_image: Optional[str] = None
    pdf: Optional[str] = None
    product: Optional[ProductMini] = None
    created_at: datetime
    updated_at: datetime
    updated_by: Optional[UserMini] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------
class PromotionBase(BaseModel):
    promotion_name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    # Which storefront advertises the deal. Defaults to machinery, so every caller
    # written before the column keeps creating machinery promotions - see
    # models.Promotion.section.
    section: Section = "machinery"
    start_date: datetime
    end_date: datetime

    @field_validator("end_date")
    @classmethod
    def _end_after_start(cls, end_date, info):
        start_date = info.data.get("start_date")
        if start_date and end_date <= start_date:
            raise ValueError("end_date must be after start_date")
        return end_date


class PromotionCreate(PromotionBase):
    price: Decimal = Field(..., gt=0)
    old_price: Optional[Decimal] = Field(None, gt=0)
    # What's inside the deal. `price` stays the promotion's own fixed bundle
    # price - it is never summed from these.
    items: list[BundleItemIn] = []


class PromotionUpdate(BaseModel):
    promotion_name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    section: Optional[Section] = None
    price: Optional[Decimal] = Field(None, gt=0)
    old_price: Optional[Decimal] = Field(None, gt=0)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    # Omitted -> contents left alone; sent (even as []) -> replaced wholesale.
    items: Optional[list[BundleItemIn]] = None


class PromotionOut(PromotionBase):
    id: int
    # Union[Decimal, str]: same masking as ProductOut.price - viewers
    # without price access get "XXXX" instead of the real value, see
    # app.routers.promotions._serialize_promotion.
    price: Union[Decimal, str]
    old_price: Optional[Union[Decimal, str]] = None
    promotion_image: Optional[str] = None
    # The member products, resolved through PromotionItem's read-through
    # properties (see BundleItemMixin in app/models.py). Not price-masked: what a
    # deal contains isn't a price, so everyone can see it.
    items: list[BundleItemOut] = []
    created_at: datetime
    updated_at: datetime
    updated_by: Optional[UserMini] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Set (a bundle deal shown on the Promotions page - see Set in app/models.py.
# Same shape as Promotion minus start_date/end_date, since a set isn't
# time-boxed.)
# ---------------------------------------------------------------------------
class SetOptionChoiceIn(BaseModel):
    product_id: int
    qty: int = Field(1, gt=0, le=MAX_QTY)
    # None = derive the upcharge from the price gap to the group's default.
    # Negative is allowed on purpose: a cheaper alternative is a valid choice.
    # See SetOptionChoice.price_delta.
    price_delta: Optional[Decimal] = None
    is_default: bool = False


class SetOptionChoiceOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    product_code: Optional[str] = None
    uom: Optional[str] = None
    qty: int
    is_default: bool
    # The stored override, null when the delta is derived. `effective_delta` is
    # the number actually charged either way - the storefront should price from
    # that one and only show this to explain where it came from.
    price_delta: Optional[Decimal] = None
    # None for a viewer without price access - an upcharge is a price like any
    # other, masked the same way price/old_price are.
    effective_delta: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


class SetOptionGroupIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    choices: list[SetOptionChoiceIn] = []


class SetOptionGroupOut(BaseModel):
    id: int
    name: str
    sort_order: int
    choices: list[SetOptionChoiceOut] = []

    model_config = ConfigDict(from_attributes=True)


class SetBase(BaseModel):
    set_name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None


class SetCreate(SetBase):
    price: Decimal = Field(..., gt=0)
    old_price: Optional[Decimal] = Field(None, gt=0)
    # Optional, unlike ProductCreate.brand_id - see Set.brand_id.
    brand_id: Optional[int] = None
    items: list[BundleItemIn] = []
    # Swappable slots - see SetOptionGroup. Empty for an ordinary fixed set.
    option_groups: list[SetOptionGroupIn] = []


class SetUpdate(BaseModel):
    set_name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    price: Optional[Decimal] = Field(None, gt=0)
    old_price: Optional[Decimal] = Field(None, gt=0)
    # Sending null clears the set's brand (back to "All" on the Promotions
    # page); omitting the field leaves it alone, as everywhere else here.
    brand_id: Optional[int] = None
    # Omitted -> contents left alone; sent (even as []) -> replaced wholesale.
    items: Optional[list[BundleItemIn]] = None
    # Same omitted-vs-sent rule as `items` above.
    option_groups: Optional[list[SetOptionGroupIn]] = None


class SetOut(SetBase):
    id: int
    # Union[Decimal, str]: same masking as ProductOut.price/PromotionOut.price
    # - viewers without price access get "XXXX" instead of the real value,
    # see app.routers.sets._serialize_set.
    price: Union[Decimal, str]
    old_price: Optional[Union[Decimal, str]] = None
    set_image: Optional[str] = None
    detail_image: Optional[str] = None
    # Same nested shape ProductOut uses, so a set card can render the brand
    # name/logo without a second lookup. None for an unbranded set.
    brand: Optional[BrandMini] = None
    # Member products - same shape/reasoning as PromotionOut.items.
    items: list[BundleItemOut] = []
    # The set's swappable slots, each with its alternatives priced. Empty list
    # for a fixed set, which is every set that predates the feature.
    option_groups: list[SetOptionGroupOut] = []
    created_at: datetime
    updated_at: datetime
    updated_by: Optional[UserMini] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Order (a finalized storefront quote - see partials/quote_drawer.html)
# ---------------------------------------------------------------------------
class OrderLineOptionIn(BaseModel):
    """One configured slot on a set line - "for the Laptop group, take choice 7".

    Ids rather than product ids: which choice was picked is what determines the
    upcharge, and two choices in different groups may well name the same product.
    """

    group_id: int
    choice_id: int


class OrderLineOptionOut(BaseModel):
    """The selection as stored on the saved line. Enough to re-send the line
    unchanged when the order is edited, which is the only reason it's persisted -
    what the customer actually receives is already spelled out by the $0
    component lines underneath."""

    group_id: int
    choice_id: int


class OrderItemCreate(BaseModel):
    """Only product_id/promotion_id/set_id + qty (+ `options` for a set) are ever
    accepted from the client - price/discount/name are always looked up and
    snapshotted server-side, see routers/orders.py. A line buys exactly one of a
    product, a promotion, or a set - exactly one of the three ids must be
    set."""

    product_id: Optional[int] = None
    promotion_id: Optional[int] = None
    set_id: Optional[int] = None
    qty: int = Field(..., gt=0, le=MAX_QTY)
    # Which alternative was picked in each of the set's option groups. Omitted or
    # partial is fine - every unmentioned group falls back to its default, so an
    # unconfigured purchase of a configurable set still works.
    options: list[OrderLineOptionIn] = []

    @model_validator(mode="after")
    def _exactly_one_id(self):
        if sum(i is not None for i in (self.product_id, self.promotion_id, self.set_id)) != 1:
            raise ValueError("Exactly one of product_id, promotion_id, or set_id must be set")
        return self

    @model_validator(mode="after")
    def _options_only_on_sets(self):
        # Only a set has option groups. Accepting them on a product line would
        # silently ignore them, which reads as "the upgrade didn't apply".
        if self.options and self.set_id is None:
            raise ValueError("options can only be sent on a set_id line")
        return self

    @model_validator(mode="after")
    def _one_choice_per_group(self):
        groups = [o.group_id for o in self.options]
        if len(groups) != len(set(groups)):
            raise ValueError("Each option group can only be chosen once per line")
        return self


class OrderCreate(BaseModel):
    # Clinic/phone/address are required on the paper quotation form; contact_person is not.
    clinic_name: str = Field(..., min_length=1, max_length=200)
    contact_person: Optional[str] = Field(None, max_length=150)
    phone: str = Field(..., min_length=1, max_length=30)
    address: str = Field(..., min_length=1, max_length=255)

    # Where to actually drive to. Optional - a pin is a convenience, never a
    # condition of buying - and normally not typed at all: the cart auto-fills it
    # from the signed-in customer's saved location (see QuoteCart.renderInfoForm
    # in EB Web Project/static/js/main.js). Accepted from the client rather than
    # read off the Customer row server-side because a staff quote has no customer
    # to read from, and because a buyer delivering somewhere other than their
    # usual address is an ordinary thing to do, not an attack - the same reason
    # contact_person stays client-supplied.
    latitude: Latitude = None
    longitude: Longitude = None
    map_link: MapLink = None

    payment_term: Optional[str] = Field(None, max_length=100)
    install_term: Optional[str] = Field(None, max_length=150)

    # salesperson/quoted_by_name are NOT accepted here - both are derived server-side from
    # whoever is actually calling (see _get_ordering_principal in routers/orders.py), never
    # trusted from the client.

    discount_type: DiscountType = "cash"
    # Decimal("0") not bare 0 - see the identical comment on ProductCreate.discount.
    discount_value: Decimal = Field(Decimal("0"), ge=0)

    # Required for customers ("cash" -> quote, "khqr" -> real order awaiting payment),
    # ignored for staff (their cart always produces a quote) - enforced in
    # routers/orders.py::create_order, not here, since only the router knows which
    # principal type is calling.
    payment_method: Optional[Literal["cash", "khqr"]] = None

    items: list[OrderItemCreate] = Field(..., min_length=1)

    @field_validator("discount_value")
    @classmethod
    def _percent_within_100(cls, discount_value, info):
        if info.data.get("discount_type") == "percent" and discount_value > 100:
            raise ValueError("A percent discount cannot exceed 100")
        return discount_value


class OrderUpdate(BaseModel):
    """What staff can still change about an order after it was placed - the quote is a
    working document until money changes hands, so the clinic details, terms, discount
    and the item list itself are all editable from the admin Orders page.

    Two hard rules live in the router, not here:
      * **A paid order is frozen.** Once payment_status is "paid" every field below is
        refused (and so is DELETE) - a receipt has been issued against those numbers.
      * **Prices are never accepted from the client.** `items` carries only
        product/promotion/set ids + qty, exactly like OrderCreate; the router re-looks-up
        and re-snapshots every price and recomputes subtotal/discount/grand_total.

    payment_status is how staff record that payment landed - the automatic Bakong/PayWay
    check for a KHQR order, or "Mark as Paid" for cash/bank transfer on any other row.
    """

    status: Optional[str] = Field(None, max_length=30)
    payment_status: Optional[Literal["unpaid", "paid"]] = None

    clinic_name: Optional[str] = Field(None, min_length=1, max_length=200)
    contact_person: Optional[str] = Field(None, max_length=150)
    phone: Optional[str] = Field(None, min_length=1, max_length=30)
    address: Optional[str] = Field(None, min_length=1, max_length=255)
    # Optional delivery pin - see the Latitude/Longitude/MapLink aliases above.
    latitude: Latitude = None
    longitude: Longitude = None
    map_link: MapLink = None
    payment_term: Optional[str] = Field(None, max_length=100)
    install_term: Optional[str] = Field(None, max_length=150)

    # Order-level discount. Sending discount_type without discount_value (or vice versa)
    # is allowed - the router fills the missing half from what's already on the order.
    discount_type: Optional[DiscountType] = None
    discount_value: Optional[Decimal] = Field(None, ge=0)

    # Omit to leave the lines alone; send a list to REPLACE them wholesale (that is how
    # the admin editor adds and removes products). The $0 component lines a bundle or
    # free-gift product expands into are regenerated server-side, so they must not be
    # sent back - only the lines actually being charged for.
    items: Optional[list[OrderItemCreate]] = Field(None, min_length=1)

    @field_validator("discount_value")
    @classmethod
    def _percent_within_100(cls, discount_value, info):
        if (
            discount_value is not None
            and info.data.get("discount_type") == "percent"
            and discount_value > 100
        ):
            raise ValueError("A percent discount cannot exceed 100")
        return discount_value


class OrderItemOut(BaseModel):
    id: int
    product_id: Optional[int] = None
    promotion_id: Optional[int] = None
    set_id: Optional[int] = None
    # Set on the $0 component lines a bundle/free-gift line expands into (see
    # OrderItem.parent_item_id). `items` below is a flat list of both kinds -
    # group by this to render components under the line they belong to.
    parent_item_id: Optional[int] = None
    product_name: str
    product_code: Optional[str] = None
    uom: Optional[str] = None
    unit_price: Decimal
    # The pre-discount unit price as it stood when the order was placed. The
    # printed quote's "UP before Discount" column reads this directly instead of
    # dividing unit_price back out - see OrderItem.list_price.
    list_price: Decimal
    discount_type: DiscountType = "percent"
    discount: Decimal
    qty: int
    line_amount: Decimal
    # Only ever populated on a configured set line - see OrderItem.set_options.
    set_options: list[OrderLineOptionOut] = []

    @field_validator("set_options", mode="before")
    @classmethod
    def _null_options_are_empty(cls, value):
        # The column is NULL on every line that isn't a configured set, and a
        # field default doesn't cover that: with from_attributes the attribute is
        # present and explicitly None, which fails list validation. Normalized
        # here so the API always returns a list and callers never branch on null.
        return value if value is not None else []

    model_config = ConfigDict(from_attributes=True)


class OrderOut(BaseModel):
    id: int
    order_number: str
    quote_code: str
    customer_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
    clinic_name: str
    contact_person: Optional[str] = None
    phone: str
    address: str
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    map_link: Optional[str] = None
    payment_term: Optional[str] = None
    salesperson: Optional[str] = None
    quoted_by_name: Optional[str] = None
    install_term: Optional[str] = None
    discount_type: DiscountType
    discount_value: Decimal
    discount_amount: Decimal
    subtotal: Decimal
    grand_total: Decimal
    status: str
    order_type: str = "order"
    payment_method: Optional[str] = None
    payment_status: Optional[str] = None
    paid_at: Optional[datetime] = None
    khqr_string: Optional[str] = None
    khqr_md5: Optional[str] = None
    # Which pending checkout this order was materialized from, for a customer KHQR sale
    # - the reference the bank knows the payment by (see PendingCheckout.reference).
    payment_reference: Optional[str] = None
    created_at: datetime
    # The staff member who last wrote to this order via PUT /orders/{id} - a status or
    # payment change, or a real edit to its details/discount/line items (only possible
    # while it is unpaid; see OrderUpdate).
    updated_at: datetime
    updated_by: Optional[UserMini] = None
    items: list[OrderItemOut] = []

    model_config = ConfigDict(from_attributes=True)


class CheckoutOut(BaseModel):
    """A KHQR checkout awaiting payment. Deliberately NOT an OrderOut: no order exists
    yet, and none will until the money arrives (see PendingCheckout). Carries only what
    the payment modal needs - the QR to render, the amount to show, and the id to poll.
    """

    id: int
    reference: str
    grand_total: Decimal
    khqr_string: str
    expires_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PendingCheckoutLineOut(BaseModel):
    """Just enough of a snapshotted line for staff to recognise what a customer is
    paying for. The full pricing only becomes visible once it is a real order."""

    product_name: str
    qty: int


class PendingCheckoutOut(BaseModel):
    """An outstanding checkout as the back office sees it - what a customer started
    paying for and has not (as far as we know) paid.

    Staff need this precisely because there is no order behind it: if automatic
    confirmation fails, this row is the only trace that money may have moved, and
    `reference` is what the payment appears as on the bank statement.
    """

    id: int
    reference: str
    customer_name: Optional[str] = None
    clinic_name: str
    phone: str
    grand_total: Decimal
    created_at: datetime
    expires_at: datetime
    # The QR's own expiry has passed, so the customer can no longer pay against it.
    # Not the same as "nothing was paid" - a payment made just before it lapsed may
    # still be settling, which is why these stay listed rather than disappearing.
    is_expired: bool
    items: list[PendingCheckoutLineOut] = []


class CheckoutStatusOut(BaseModel):
    """What the payment poll returns.

    `payment_status` is "unpaid" while waiting, "paid" once confirmed, or "expired" when
    the QR's own expiry passed with no payment. `order` is filled in on exactly the
    transition to "paid" and on every poll after it - that is the moment the order comes
    into existence, and it is what the browser renders the receipt from.
    """

    payment_status: Literal["unpaid", "paid", "expired"]
    order: Optional[OrderOut] = None


# ---------------------------------------------------------------------------
# Site-wide settings (see app/core/settings_spec.py)
# ---------------------------------------------------------------------------
class SettingsUpdate(BaseModel):
    """A partial dict of {key: value}. Types are deliberately loose here - `Any` rather
    than a field per setting - because the real validation is `settings_spec.coerce()`,
    which is also what the admin form is generated from. Declaring the shape twice is
    exactly the drift this design exists to avoid."""

    values: dict[str, Any] = Field(default_factory=dict)


class SettingsReset(BaseModel):
    """Put settings back on their defaults: a whole `group`, an explicit list of `keys`,
    or both. At least one must be non-empty (enforced in the router, so the error names
    what to send instead of a Pydantic shape complaint)."""

    group: Optional[str] = None
    keys: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Department QR codes (contact page)
# ---------------------------------------------------------------------------
# The badge colours the storefront actually has CSS for - see .qr-badge in the Flask
# app's static/css/products.css. "" is the default cyan. A Literal rather than a DB
# enum so a fifth colour is a code change here plus one CSS rule, with no migration.
QrBadgeVariant = Literal["", "machinery", "materials", "implants"]


class QrCodeUpdate(BaseModel):
    """Partial update. Every field is optional and applied with `exclude_unset`, so
    omitting a key leaves it alone while sending `null` clears it - which is how the
    admin form erases a subtitle or a badge."""

    title: Optional[str] = Field(None, min_length=1, max_length=150)
    subtitle: Optional[str] = Field(None, max_length=200)
    badge_label: Optional[str] = Field(None, max_length=60)
    badge_variant: Optional[QrBadgeVariant] = None
    badge_icon: Optional[str] = Field(None, max_length=60)
    sort_order: Optional[int] = None


class QrCodeOut(BaseModel):
    id: int
    title: str
    subtitle: Optional[str] = None
    qr_image: Optional[str] = None
    badge_label: Optional[str] = None
    badge_variant: Optional[str] = None
    badge_icon: Optional[str] = None
    sort_order: int
    created_at: datetime
    updated_at: datetime
    updated_by: Optional[UserMini] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Hero slides (the storefront's rotating banner)
# ---------------------------------------------------------------------------


class HeroSlideUpdate(BaseModel):
    """Partial update. Every field is optional and applied with `exclude_unset`, so
    omitting a key leaves it alone while sending `null` clears it - which is how the
    admin form erases a badge, a subheading or a button."""

    heading: Optional[str] = Field(None, min_length=1, max_length=200)
    heading_highlight: Optional[str] = Field(None, max_length=120)
    subheading: Optional[str] = Field(None, max_length=400)
    # Which carousel the slide belongs in. Not Optional-meaning-clear like the text
    # fields around it: a slide is always in one shop or the other, so there is no
    # "null" to clear it to.
    section: Optional[Section] = None
    badge_label: Optional[str] = Field(None, max_length=60)
    badge_icon: Optional[str] = Field(None, max_length=60)
    button_label: Optional[str] = Field(None, max_length=60)
    button_url: Optional[str] = Field(None, max_length=500)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class HeroSlideOut(BaseModel):
    id: int
    heading: str
    heading_highlight: Optional[str] = None
    subheading: Optional[str] = None
    section: Section = "machinery"
    slide_image: Optional[str] = None
    badge_label: Optional[str] = None
    badge_icon: Optional[str] = None
    button_label: Optional[str] = None
    button_url: Optional[str] = None
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime
    updated_by: Optional[UserMini] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------
class ActivityLogOut(BaseModel):
    """One recorded change, as the admin screens read it.

    `changes` is deliberately loose - `dict[str, list]` of {field: [old, new]} - and
    not a typed model per entity. The log spans twenty tables whose columns are
    strings, numbers, dates and JSON; pinning a schema onto that would mean a schema
    change every time a column is added, which is exactly the coupling the listener
    exists to avoid. The screens render it as text either way.
    """

    id: int
    occurred_at: datetime
    actor_type: Literal["user", "customer", "system"]
    actor_user_id: Optional[int] = None
    actor_customer_id: Optional[int] = None
    # The name as it was, not a join to the account - which may have been renamed or
    # deleted since. See the column comment in models.py.
    actor_label: Optional[str] = None
    action: str
    entity_type: str
    entity_id: Optional[int] = None
    entity_label: Optional[str] = None
    changes: Optional[dict[str, Any]] = None
    note: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ActivityLogPage(BaseModel):
    """An envelope rather than a bare list, which every other endpoint here returns.

    The log is the one table with no natural ceiling - products and orders are counted
    in hundreds, this grows forever - so its screen needs a real pager, and a pager
    needs to know how many rows the filter matched. `total` is the count BEFORE
    skip/limit.
    """

    items: list[ActivityLogOut]
    total: int
