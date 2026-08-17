"""
Pydantic schemas used for request validation and response serialization.

Naming convention used throughout:
  *Create        -> payload to create a new record
  *Update        -> payload to update an existing record (all fields optional)
  *Out           -> what gets returned to the client
"""
from datetime import date, datetime, timezone
from decimal import Decimal
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


class CategoryUpdate(BaseModel):
    category_name: Optional[str] = Field(None, min_length=1, max_length=150)


class CategoryOut(CategoryBase):
    id: int
    category_image: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    updated_by: Optional[UserMini] = None

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
    updated_at: datetime
    updated_by: Optional[UserMini] = None

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
    updated_at: datetime
    updated_by: Optional[UserMini] = None

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
    # Optional, unlike ProductCreate.brand_id - see Set.brand_id.
    brand_id: Optional[int] = None
    items: list[BundleItemIn] = []


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
    created_at: datetime
    updated_at: datetime
    updated_by: Optional[UserMini] = None

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
    qty: int = Field(..., gt=0, le=MAX_QTY)

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
