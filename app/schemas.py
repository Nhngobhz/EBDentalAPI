"""
Pydantic schemas used for request validation and response serialization.

Naming convention used throughout:
  *Create        -> payload to create a new record
  *Update        -> payload to update an existing record (all fields optional)
  *Out           -> what gets returned to the client
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal, Optional, Union

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
    access_permission: Optional[bool] = None


class CustomerOut(CustomerBase):
    id: int
    customer_image: Optional[str] = None
    access_permission: bool
    is_active: bool
    is_verified: bool
    creation_date: datetime

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


class CategoryUpdate(BaseModel):
    category_name: Optional[str] = Field(None, min_length=1, max_length=150)


class CategoryOut(CategoryBase):
    id: int
    category_image: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CategoryMini(BaseModel):
    """Small nested representation used inside ProductOut."""

    id: int
    category_name: str
    category_image: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------
# Shared by Product and Order - either a percentage (0-100) or a flat $ amount off,
# depending on context. Declared once here (rather than separately near OrderCreate)
# since ProductBase needs it first in file order.
DiscountType = Literal["percent", "cash"]


# ---------------------------------------------------------------------------
# Bundle contents - the shared "this thing contains these products" shape used
# by Promotion.items, Set.items and Product.free_items (see BundleItemMixin in
# app/models.py). Only product_id + qty are ever accepted; everything shown back
# is read from the live Product row, so renaming a product updates every bundle
# it appears in.
# ---------------------------------------------------------------------------
class BundleItemIn(BaseModel):
    product_id: int
    qty: int = Field(1, gt=0)


class BundleItemOut(BaseModel):
    product_id: int
    product_name: str
    product_code: Optional[str] = None
    uom: Optional[str] = None
    qty: int

    model_config = ConfigDict(from_attributes=True)


class ProductBase(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    badge: Optional[str] = Field(None, max_length=50)
    product_code: Optional[str] = Field(None, max_length=50)
    uom: Optional[str] = Field(None, max_length=20)


class ProductCreate(ProductBase):
    price: Decimal = Field(..., gt=0)
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
    brand_id: Optional[int] = None
    category_id: Optional[int] = None
    # Present here so a product_management holder *can* still create a
    # product with a price, but changing price/discount on an *existing*
    # product additionally requires the price_listing permission - enforced
    # in the router, not here.
    price: Optional[Decimal] = Field(None, gt=0)
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
    discount_type: Optional[DiscountType] = None
    discount: Optional[Decimal] = Field(None, ge=0)

    @field_validator("discount")
    @classmethod
    def _percent_within_100(cls, discount, info):
        if discount is not None and info.data.get("discount_type") == "percent" and discount > 100:
            raise ValueError("A percent discount cannot exceed 100")
        return discount


class ProductOut(ProductBase):
    id: int
    # Union[Decimal, str]: viewers without price access (see
    # app.core.deps.get_price_visibility) get back the literal string
    # "XXXX" instead of the real value.
    price: Union[Decimal, str]
    discount_type: DiscountType = "percent"
    # Masked to None for the same viewers, same reasoning as price - see
    # app.routers.products._serialize_product.
    discount: Optional[Decimal] = None
    product_image: Optional[str] = None
    brand: Optional[BrandMini] = None
    category: Optional[CategoryMini] = None
    free_items: list[BundleItemOut] = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Manual
# ---------------------------------------------------------------------------
class ManualBase(BaseModel):
    description: Optional[str] = None


class ManualUpdate(BaseModel):
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

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------
class PromotionBase(BaseModel):
    promotion_name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
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

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Set (a bundle deal shown on the Promotions page - see Set in app/models.py.
# Same shape as Promotion minus start_date/end_date, since a set isn't
# time-boxed.)
# ---------------------------------------------------------------------------
class SetBase(BaseModel):
    set_name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None


class SetCreate(SetBase):
    price: Decimal = Field(..., gt=0)
    old_price: Optional[Decimal] = Field(None, gt=0)
    items: list[BundleItemIn] = []


class SetUpdate(BaseModel):
    set_name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    price: Optional[Decimal] = Field(None, gt=0)
    old_price: Optional[Decimal] = Field(None, gt=0)
    # Omitted -> contents left alone; sent (even as []) -> replaced wholesale.
    items: Optional[list[BundleItemIn]] = None


class SetOut(SetBase):
    id: int
    # Union[Decimal, str]: same masking as ProductOut.price/PromotionOut.price
    # - viewers without price access get "XXXX" instead of the real value,
    # see app.routers.sets._serialize_set.
    price: Union[Decimal, str]
    old_price: Optional[Union[Decimal, str]] = None
    set_image: Optional[str] = None
    detail_image: Optional[str] = None
    # Member products - same shape/reasoning as PromotionOut.items.
    items: list[BundleItemOut] = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Order (a finalized storefront quote - see partials/quote_drawer.html)
# ---------------------------------------------------------------------------
class OrderItemCreate(BaseModel):
    """Only product_id/promotion_id/set_id + qty are ever accepted from the
    client - price/discount/name are always looked up and snapshotted
    server-side, see routers/orders.py. A line buys exactly one of a
    product, a promotion, or a set - exactly one of the three ids must be
    set."""

    product_id: Optional[int] = None
    promotion_id: Optional[int] = None
    set_id: Optional[int] = None
    qty: int = Field(..., gt=0)

    @model_validator(mode="after")
    def _exactly_one_id(self):
        if sum(i is not None for i in (self.product_id, self.promotion_id, self.set_id)) != 1:
            raise ValueError("Exactly one of product_id, promotion_id, or set_id must be set")
        return self


class OrderCreate(BaseModel):
    # Clinic/phone/address are required on the paper quotation form; contact_person is not.
    clinic_name: str = Field(..., min_length=1, max_length=200)
    contact_person: Optional[str] = Field(None, max_length=150)
    phone: str = Field(..., min_length=1, max_length=30)
    address: str = Field(..., min_length=1, max_length=255)
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
    """The only things staff can change after the fact - everything else is an
    immutable record of what was actually quoted/sold. payment_status exists so
    staff can manually confirm a KHQR payment (admin Orders page "Mark as Paid")
    when automatic Bakong checking isn't configured - the router rejects it on
    non-KHQR rows."""

    status: Optional[str] = Field(None, max_length=30)
    payment_status: Optional[Literal["unpaid", "paid"]] = None


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
    discount_type: DiscountType = "percent"
    discount: Decimal
    qty: int
    line_amount: Decimal

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
    created_at: datetime
    items: list[OrderItemOut] = []

    model_config = ConfigDict(from_attributes=True)
