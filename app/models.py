"""
SQLAlchemy ORM models.

These map directly to the tables requested, with a few deliberate additions
needed to make authentication / RBAC / email confirmation actually work.
Every addition or naming fix is called out in a comment and summarized in
README.md under "Design decisions & assumptions".
"""
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import backref, relationship
from sqlalchemy.sql import func

from app.database import Base


class BundleItemMixin:
    """Shared shape of the three "this thing contains these products" join
    tables - PromotionItem, SetItem, ProductFreeItem. Each row is always
    (owner, product_id, qty), so these read-through properties let any of them
    be serialized straight into BundleItemOut (see app/schemas.py) without the
    router re-joining Product itself.

    A member product's name/code/uom are deliberately NOT copied onto the join
    row: unlike OrderItem (a historical snapshot that must never change), a
    bundle's contents should always describe the product as it is *today*.
    Deleting a product removes its membership rows via ON DELETE CASCADE, so
    `product` being None shouldn't happen - the fallbacks just keep an
    in-session delete from raising mid-serialization."""

    @property
    def product_name(self) -> str:
        return self.product.product_name if self.product else ""

    @property
    def product_code(self):
        return self.product.product_code if self.product else None

    @property
    def uom(self):
        return self.product.uom if self.product else None


class User(Base):
    """Staff / admin accounts. This is the table the whole auth + RBAC
    system is built around."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    user_name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    address = Column(String(255), nullable=True)
    phone_num = Column(String(30), nullable=True)
    user_image = Column(String(500), nullable=True)  # path/URL to an image
    role_title = Column(String(100), nullable=False, default="Staff")
    creation_date = Column(DateTime(timezone=True), server_default=func.now())

    # --- Permissions (the actual RBAC source of truth) ---------------------
    # role_title is a free-text label (e.g. "Sales Manager"). What a user
    # can actually DO is controlled by these four explicit flags.
    user_management = Column(Boolean, default=False, nullable=False)
    price_listing = Column(Boolean, default=False, nullable=False)
    product_management = Column(Boolean, default=False, nullable=False)
    customer_management = Column(Boolean, default=False, nullable=False)

    # --- Added: required to support password auth / email confirmation -----
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    verification_token = Column(String(255), nullable=True, unique=True, index=True)
    verification_token_expires = Column(DateTime(timezone=True), nullable=True)
    reset_token = Column(String(255), nullable=True, unique=True, index=True)
    reset_token_expires = Column(DateTime(timezone=True), nullable=True)
    last_login = Column(DateTime(timezone=True), nullable=True)


class Customer(Base):
    """Customers can either be plain records managed by staff (created via
    POST /customers/, no password) or self-service accounts that can log
    in (created via POST /auth/customer/register). `access_permission`
    gates whether a logged-in customer can see product prices - it only
    ever changes via a customer_management staff member."""

    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)

    customer_name = Column(String(150), nullable=False, index=True)  # fixed: customer_ame
    email = Column(String(255), unique=True, nullable=False, index=True)
    address = Column(String(255), nullable=True)
    phone_num = Column(String(30), nullable=True)  # fixed casing: phone_Num
    customer_image = Column(String(500), nullable=True)
    access_permission = Column(Boolean, default=False, nullable=False)
    creation_date = Column(DateTime(timezone=True), server_default=func.now())

    # --- Added: self-service login support ---------------------------------
    # NULL hashed_password means this Customer record was created by staff
    # and has no login capability - only self-registered customers
    # (POST /auth/customer/register) can authenticate.
    hashed_password = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    verification_token = Column(String(255), nullable=True, unique=True, index=True)
    verification_token_expires = Column(DateTime(timezone=True), nullable=True)
    reset_token = Column(String(255), nullable=True, unique=True, index=True)
    reset_token_expires = Column(DateTime(timezone=True), nullable=True)
    last_login = Column(DateTime(timezone=True), nullable=True)


class Brand(Base):
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True, index=True)
    brand_name = Column(String(150), unique=True, nullable=False, index=True)
    brand_image = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    products = relationship("Product", back_populates="brand")


class Category(Base):
    """Product category (e.g. "Curing Light", "Trolley"). Same shape/role as
    Brand - a proper table instead of a free-text column, for the same
    reason brand_name became brand_id (see README)."""

    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    category_name = Column(String(150), unique=True, nullable=False, index=True)
    category_image = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # passive_deletes=True: category_id is nullable (unlike brand_id), so
    # without this SQLAlchemy would silently UPDATE ... SET category_id =
    # NULL on referencing products before deleting the category instead of
    # letting the DB's ON DELETE RESTRICT reject it - defeating the "can't
    # delete a category still in use" rule that Brand gets "for free" only
    # because brand_id happens to be NOT NULL.
    products = relationship("Product", back_populates="category", passive_deletes=True)


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    product_name = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)
    price = Column(Numeric(10, 2), nullable=False)

    # Either a percentage (0-100) or a flat $ amount off price, per discount_type -
    # replaces a separate old_price column (see ProductBase.discount for range
    # validation). Not a value derived from anything else stored; Numeric (not Integer)
    # so a cash discount can hold cents.
    discount_type = Column(String(10), nullable=False, server_default="percent")
    discount = Column(Numeric(10, 2), nullable=False, server_default="0")

    # Changed from a raw `brand_name` string to a foreign key. See README:
    # "Product.brand_name -> brand_id".
    brand_id = Column(Integer, ForeignKey("brands.id", ondelete="RESTRICT"), nullable=False)

    # Changed from a raw `category` string to a foreign key, same reasoning
    # as brand_id. Nullable (unlike brand_id) since not every product has
    # been sorted into a category yet.
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True)

    # SKU / manufacturer code. Unique once set (see products.py router for
    # the pre-insert/update duplicate check - the DB constraint alone would
    # otherwise surface as a raw 500), but nullable since existing products
    # predate this field and not all of them have a code assigned yet.
    product_code = Column(String(50), unique=True, nullable=True, index=True)

    # Unit of measure the product is sold/counted in (e.g. "pcs", "box",
    # "set"). Free text like badge - no fixed vocabulary requested.
    uom = Column(String(20), nullable=True)

    badge = Column(String(50), nullable=True)
    product_image = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    brand = relationship("Brand", back_populates="products")
    category = relationship("Category", back_populates="products")
    manuals = relationship("Manual", back_populates="product", cascade="all, delete-orphan")

    # Other products this one comes with for free (see ProductFreeItem) - added to
    # the order as $0 lines whenever this product is bought.
    free_items = relationship(
        "ProductFreeItem",
        foreign_keys="ProductFreeItem.parent_product_id",
        back_populates="parent_product",
        cascade="all, delete-orphan",
        order_by="ProductFreeItem.id",
    )


class ProductFreeItem(BundleItemMixin, Base):
    """A free product that comes with a paid one ("buy this, get that free").
    Both sides are real Products - the freebie is never a separate free-text
    line, so its name/code/uom always stay in sync with the catalog.

    Note which column is which: `product_id` is the FREE product (uniform with
    PromotionItem/SetItem, so BundleItemMixin works for all three), and
    `parent_product_id` is the paid product it rides along with."""

    __tablename__ = "product_free_items"
    __table_args__ = (
        UniqueConstraint("parent_product_id", "product_id", name="uq_product_free_item"),
        CheckConstraint("parent_product_id <> product_id", name="ck_product_free_item_not_self"),
    )

    id = Column(Integer, primary_key=True, index=True)
    parent_product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # CASCADE (not RESTRICT): deleting a product that happens to be somebody's
    # freebie just drops it from that bundle rather than blocking the delete -
    # historical orders are unaffected, they carry their own snapshot.
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qty = Column(Integer, nullable=False, server_default="1")

    parent_product = relationship(
        "Product", foreign_keys=[parent_product_id], back_populates="free_items"
    )
    product = relationship("Product", foreign_keys=[product_id])


class Manual(Base):
    __tablename__ = "manuals"

    id = Column(Integer, primary_key=True, index=True)

    # Changed from a raw `product_name` string to a foreign key, same
    # reasoning as Product.brand_id. See README.
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)

    description = Column(Text, nullable=True)
    manual_image = Column(String(500), nullable=True)
    pdf = Column(String(500), nullable=True)  # path/URL to the PDF file
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    product = relationship("Product", back_populates="manuals")


class Promotion(Base):
    """A time-boxed deal, and a COLLECTION OF PRODUCTS (see PromotionItem):
    the promotion carries its own fixed price, and buying it puts every member
    product on the order as a $0 component line so the quote spells out exactly
    what the customer gets. `price` is always the admin-entered bundle price -
    it is never summed from the members."""

    __tablename__ = "promotions"

    id = Column(Integer, primary_key=True, index=True)
    promotion_name = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)
    price = Column(Numeric(10, 2), nullable=False)
    old_price = Column(Numeric(10, 2), nullable=True)  # fixed: old-price -> old_price
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    promotion_image = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    items = relationship(
        "PromotionItem",
        back_populates="promotion",
        cascade="all, delete-orphan",
        order_by="PromotionItem.id",
    )


class PromotionItem(BundleItemMixin, Base):
    """One product included in a Promotion, with how many of it the deal
    includes. Contents only - the money lives on Promotion.price."""

    __tablename__ = "promotion_items"
    __table_args__ = (UniqueConstraint("promotion_id", "product_id", name="uq_promotion_item"),)

    id = Column(Integer, primary_key=True, index=True)
    promotion_id = Column(
        Integer, ForeignKey("promotions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qty = Column(Integer, nullable=False, server_default="1")

    promotion = relationship("Promotion", back_populates="items")
    product = relationship("Product")


class Set(Base):
    """A bundle deal shown on the Promotions page, styled like a Promotion
    (fixed price + optional old_price, image) but always on sale - unlike
    Promotion there's no start_date/end_date, since a set isn't time-boxed.
    Also a collection of products (see SetItem), same as Promotion. Bought the
    same way a Promotion is (see OrderItem.set_id)."""

    __tablename__ = "sets"

    id = Column(Integer, primary_key=True, index=True)
    set_name = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)
    price = Column(Numeric(10, 2), nullable=False)
    old_price = Column(Numeric(10, 2), nullable=True)
    set_image = Column(String(500), nullable=True)
    # Optional second image shown under the name/description on the storefront
    # set card - e.g. a shot of what's inside the bundle. Purely decorative:
    # a set without one just renders the card without it.
    detail_image = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    items = relationship(
        "SetItem",
        back_populates="set",
        cascade="all, delete-orphan",
        order_by="SetItem.id",
    )


class SetItem(BundleItemMixin, Base):
    """One product included in a Set - same shape and reasoning as
    PromotionItem."""

    __tablename__ = "set_items"
    __table_args__ = (UniqueConstraint("set_id", "product_id", name="uq_set_item"),)

    id = Column(Integer, primary_key=True, index=True)
    set_id = Column(Integer, ForeignKey("sets.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    qty = Column(Integer, nullable=False, server_default="1")

    set = relationship("Set", back_populates="items")
    product = relationship("Product")


class Order(Base):
    """A finalized storefront quote (see partials/quote_drawer.html on the frontend).
    Created only through the order-creation endpoint, which snapshots each line item's
    product data server-side - never from client-submitted prices (see OrderItem)."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(20), unique=True, nullable=False, index=True)

    # "C. Code" on the paper quotation form - randomly generated server-side per quote
    # (see _generate_quote_code in routers/orders.py), distinct from order_number (which
    # is sequential). Never client-supplied.
    quote_code = Column(String(20), unique=True, nullable=False, index=True)

    # Nullable: an order may belong to a registered Customer, be recorded by a staff
    # member on a walk-in clinic's behalf with no account, or both.
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Free-text snapshot of the quote drawer's info form - describes a specific
    # clinic/contact at a point in time, not a live-linked record (same reasoning as
    # badge/role_title being free text elsewhere in this schema). Clinic/phone/address are
    # required (unlike contact_person) - see OrderCreate.
    clinic_name = Column(String(200), nullable=False)
    contact_person = Column(String(150), nullable=True)
    phone = Column(String(30), nullable=False)
    address = Column(String(255), nullable=False)
    payment_term = Column(String(100), nullable=True)

    # "Salesperson" on the paper form - always server-derived, never typed in: the acting
    # staff member's name, or "Website" for a customer placing their own order (see
    # _get_ordering_principal / create_order in routers/orders.py).
    salesperson = Column(String(150), nullable=True)

    # "User" on the paper form - the display name of whoever was actually logged in when
    # the quote was placed (staff user_name or customer_name). Distinct from salesperson:
    # this one is never overridden to "Website" for a customer.
    quoted_by_name = Column(String(150), nullable=True)

    install_term = Column(String(150), nullable=True)

    # Order-level discount: either a percentage or a flat cash amount, per discount_type.
    # discount_value is the raw number the admin entered; discount_amount is the actual
    # computed $ figure subtracted from subtotal, persisted so printing/auditing never has
    # to recompute the math (and so a later change to how % is calculated can't silently
    # change what a historical order's PDF shows). Replaces the old flat-cash-only
    # cash_discount column.
    discount_type = Column(String(10), nullable=False, server_default="cash")
    discount_value = Column(Numeric(10, 2), nullable=False, server_default="0")
    discount_amount = Column(Numeric(10, 2), nullable=False, server_default="0")

    subtotal = Column(Numeric(10, 2), nullable=False)
    grand_total = Column(Numeric(10, 2), nullable=False)

    # Free text, same pattern as Product.badge/User.role_title - no fixed workflow was
    # requested. Defaults to "pending"; staff can update it via PUT /orders/{id}.
    status = Column(String(30), nullable=False, server_default="pending")

    # "quote" or "order". Staff always produce quotes (their cart IS the quote tool),
    # and a customer choosing Cash also gets a quote (payment happens offline later).
    # Only a customer checking out with KHQR creates a real "order" - see
    # routers/orders.py::create_order. Existing rows were backfilled to "quote" by the
    # migration (nothing placed before this column existed ever took a payment).
    order_type = Column(String(10), nullable=False, server_default="order")

    # NULL for staff quotes (no payment concept there); "cash" or "khqr" for
    # customer-placed rows. payment_status/paid_at only ever apply to KHQR orders:
    # "unpaid" until the Bakong transaction is confirmed (GET /orders/{id}/payment-status
    # polling) or staff mark it paid, then "paid".
    payment_method = Column(String(10), nullable=True)
    payment_status = Column(String(20), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)

    # The generated KHQR (EMV) payload string shown to the customer as a QR code, and
    # its MD5 - the MD5 is what Bakong's check_transaction_by_md5 API keys on, so both
    # are persisted with the order (see app/services/khqr.py).
    khqr_string = Column(String(512), nullable=True)
    khqr_md5 = Column(String(32), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    customer = relationship("Customer")
    created_by = relationship("User")
    # Ordered for DISPLAY, not by insert order. Plain id order would be wrong:
    # SQLAlchemy inserts every paid line before any component line (components
    # depend on their parent's id), so the $0 lines would all pile up at the end
    # instead of sitting under the line they belong to. Grouping by
    # coalesce(parent_item_id, id) puts each group together, and within a group
    # the parent always has the lowest id - it has to exist before its
    # components can reference it - so it still comes first.
    items = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="[func.coalesce(OrderItem.parent_item_id, OrderItem.id), OrderItem.id]",
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)

    # SET NULL (not RESTRICT): deleting a product must never block or corrupt historical
    # orders - the snapshot fields below are what actually matters once an order exists.
    # Exactly one of product_id/promotion_id/set_id is set per line (see
    # OrderItemCreate) - a promotion or set is bought the same way a product is, just
    # priced from Promotion/Set instead.
    product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    promotion_id = Column(Integer, ForeignKey("promotions.id", ondelete="SET NULL"), nullable=True)
    set_id = Column(Integer, ForeignKey("sets.id", ondelete="SET NULL"), nullable=True)

    # Set on a COMPONENT line - the $0 rows that spell out what a paid line
    # includes: the member products a Promotion/Set expands into, and the
    # freebies a Product "comes with" (see PromotionItem/SetItem/ProductFreeItem).
    # NULL on a normal, independently-priced line. A component always carries
    # unit_price/discount/line_amount = 0, so it can never move subtotal,
    # discount or grand_total - it exists purely so the quote/invoice lists what
    # the customer actually receives. Clients never send these: create_order
    # expands them server-side from the bundle's current contents.
    parent_item_id = Column(
        Integer, ForeignKey("order_items.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Snapshotted at order-creation time from the Product/Promotion/Set row - never
    # re-derived later, so a historical order stays accurate even if the product's
    # price/name/code changes or the product/promotion/set itself is deleted. Reused
    # as-is for a promotion/set line (product_name holds promotion_name/set_name,
    # product_code/uom stay null - neither has them).
    product_name = Column(String(200), nullable=False)
    product_code = Column(String(50), nullable=True)
    uom = Column(String(20), nullable=True)
    unit_price = Column(Numeric(10, 2), nullable=False)
    # Snapshotted from Product.discount/discount_type at quote time (see
    # routers/orders.py::create_order) - same percent-or-cash shape as the product it
    # came from, so the printed quote's "Discount" column stays meaningful even after the
    # product's own discount later changes.
    discount_type = Column(String(10), nullable=False, server_default="percent")
    discount = Column(Numeric(10, 2), nullable=False, server_default="0")
    qty = Column(Integer, nullable=False)
    line_amount = Column(Numeric(10, 2), nullable=False)

    order = relationship("Order", back_populates="items")
    # Adjacency list: the parent line owns its components, so building an order can
    # just assign them and let SQLAlchemy insert the parent first and fill in
    # parent_item_id. Components are ALSO in Order.items (they carry order_id like any
    # other row), which is what keeps OrderOut.items a flat, complete list.
    components = relationship(
        "OrderItem",
        cascade="all, delete-orphan",
        backref=backref("parent", remote_side=[id]),
        order_by="OrderItem.id",
    )
