"""
Pydantic schemas used for request validation and response serialization.

Naming convention used throughout:
  *Create        -> payload to create a new record
  *Update        -> payload to update an existing record (all fields optional)
  *Out           -> what gets returned to the client
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


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


# ---------------------------------------------------------------------------
# User (staff / admin accounts)
# ---------------------------------------------------------------------------
class UserBase(BaseModel):
    user_name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    address: Optional[str] = Field(None, max_length=255)
    phone_num: Optional[str] = Field(None, max_length=30)


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


class ChangePassword(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=72)
    new_password: str = Field(..., min_length=8, max_length=72)


class UserUpdateByAdmin(BaseModel):
    user_name: Optional[str] = Field(None, min_length=2, max_length=100)
    address: Optional[str] = Field(None, max_length=255)
    phone_num: Optional[str] = Field(None, max_length=30)
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


class CustomerLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    customer: CustomerOut


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
# ProductType values are validated here rather than with a DB-level enum,
# so adding a new one later (e.g. "bundle") is a one-line change, not a
# migration - see Product.product_type in app/models.py.
ProductType = Literal["single", "combo"]

class ProductBase(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    badge: Optional[str] = Field(None, max_length=50)
    product_type: ProductType = "single"
    product_code: Optional[str] = Field(None, max_length=50)
    uom: Optional[str] = Field(None, max_length=20)


class ProductCreate(ProductBase):
    price: Decimal = Field(..., gt=0)
    discount: int = Field(0, ge=0, le=100)
    brand_id: int
    category_id: Optional[int] = None


class ProductUpdate(BaseModel):
    product_name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    badge: Optional[str] = Field(None, max_length=50)
    product_type: Optional[ProductType] = None
    product_code: Optional[str] = Field(None, max_length=50)
    uom: Optional[str] = Field(None, max_length=20)
    brand_id: Optional[int] = None
    category_id: Optional[int] = None
    # Present here so a product_management holder *can* still create a
    # product with a price, but changing price/discount on an *existing*
    # product additionally requires the price_listing permission - enforced
    # in the router, not here.
    price: Optional[Decimal] = Field(None, gt=0)
    discount: Optional[int] = Field(None, ge=0, le=100)


class ProductPriceUpdate(BaseModel):
    price: Optional[Decimal] = Field(None, gt=0)
    discount: Optional[int] = Field(None, ge=0, le=100)


class ProductOut(ProductBase):
    id: int
    # Union[Decimal, str]: viewers without price access (see
    # app.core.deps.get_price_visibility) get back the literal string
    # "XXXX" instead of the real value.
    price: Union[Decimal, str]
    # Masked to None for the same viewers, same reasoning as price - see
    # app.routers.products._serialize_product.
    discount: Optional[int] = None
    product_image: Optional[str] = None
    brand: Optional[BrandMini] = None
    category: Optional[CategoryMini] = None
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


class PromotionUpdate(BaseModel):
    promotion_name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    price: Optional[Decimal] = Field(None, gt=0)
    old_price: Optional[Decimal] = Field(None, gt=0)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class PromotionOut(PromotionBase):
    id: int
    # Union[Decimal, str]: same masking as ProductOut.price - viewers
    # without price access get "XXXX" instead of the real value, see
    # app.routers.promotions._serialize_promotion.
    price: Union[Decimal, str]
    old_price: Optional[Union[Decimal, str]] = None
    promotion_image: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Order (a finalized storefront quote - see partials/quote_drawer.html)
# ---------------------------------------------------------------------------
class OrderItemCreate(BaseModel):
    """Only product_id + qty are ever accepted from the client - price/discount/name
    are always looked up and snapshotted server-side, see routers/orders.py."""

    product_id: int
    qty: int = Field(..., gt=0)


class OrderCreate(BaseModel):
    clinic_name: Optional[str] = Field(None, max_length=200)
    contact_person: Optional[str] = Field(None, max_length=150)
    phone: Optional[str] = Field(None, max_length=30)
    address: Optional[str] = Field(None, max_length=255)
    payment_term: Optional[str] = Field(None, max_length=100)
    salesperson: Optional[str] = Field(None, max_length=150)
    install_term: Optional[str] = Field(None, max_length=150)
    cash_discount: Decimal = Field(0, ge=0)
    items: list[OrderItemCreate] = Field(..., min_length=1)


class OrderUpdate(BaseModel):
    """The only thing staff can change after the fact - everything else is an
    immutable record of what was actually quoted/sold."""

    status: Optional[str] = Field(None, max_length=30)


class OrderItemOut(BaseModel):
    id: int
    product_id: Optional[int] = None
    product_name: str
    product_code: Optional[str] = None
    uom: Optional[str] = None
    unit_price: Decimal
    discount: int
    qty: int
    line_amount: Decimal

    model_config = ConfigDict(from_attributes=True)


class OrderOut(BaseModel):
    id: int
    order_number: str
    customer_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
    clinic_name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_term: Optional[str] = None
    salesperson: Optional[str] = None
    install_term: Optional[str] = None
    cash_discount: Decimal
    subtotal: Decimal
    grand_total: Decimal
    status: str
    created_at: datetime
    items: list[OrderItemOut] = []

    model_config = ConfigDict(from_attributes=True)
