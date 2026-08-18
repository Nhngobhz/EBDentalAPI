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
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import backref, relationship
from sqlalchemy.sql import func

from app.database import Base


class AuditedMixin:
    """`updated_at` + `updated_by_user_id` for every editable entity table.

    A mixin rather than two columns copied nine times, so the pair can't drift
    apart (and `declared_attr` is what lets a ForeignKey be reused across
    classes - a bare Column object can only belong to one table).

    `updated_at` is maintained by `onupdate`, i.e. by SQLAlchemy on flush, not by
    a database trigger. That's sufficient because every write in this system goes
    through the ORM, but it has two consequences worth knowing: a hand-written
    `UPDATE` in psql won't move it, and it moves on ANY write to the row - so a
    login bumps a `User`'s updated_at (it stamps `last_login`) even though nobody
    edited anything. Read it as "row last written", not "someone changed a field".

    `updated_by_user_id` is the staff member who last wrote to the row, and it is
    only as precise as that sounds: it's overwritten by the next write to ANY
    field, and it never records the previous value. NULL is normal - a customer
    editing their own profile isn't a `User`, and rows predating the column have
    nobody recorded. See the f2a9c4e18b73 migration for the fuller caveat.
    """

    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @declared_attr
    def updated_by_user_id(cls):
        return Column(
            Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        )

    @declared_attr
    def updated_by(cls):
        # foreign_keys spelled out because `users` itself uses this mixin, which
        # gives that table two paths to users.id (its own PK and this FK).
        return relationship(
            "User", foreign_keys=lambda: [cls.updated_by_user_id], remote_side="User.id"
        )


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


class User(AuditedMixin, Base):
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

    # --- Optional demographics ---------------------------------------------
    # Same pair, same rules, as Customer.date_of_birth/gender below - see the
    # comment there. Kept as two separate columns per table rather than a
    # shared mixin because `users` and `customers` are independent principal
    # types with no common base.
    date_of_birth = Column(Date, nullable=True)
    gender = Column(String(10), nullable=True)

    # --- Permissions (the actual RBAC source of truth) ---------------------
    # role_title is a free-text label (e.g. "Sales Manager"). What a user
    # can actually DO is controlled by these explicit flags.
    user_management = Column(Boolean, default=False, nullable=False)
    price_listing = Column(Boolean, default=False, nullable=False)
    product_management = Column(Boolean, default=False, nullable=False)
    customer_management = Column(Boolean, default=False, nullable=False)
    # Site-wide configuration (the Settings screen / AppSetting below). Deliberately
    # separate from user_management: being allowed to create staff accounts is not the
    # same as being allowed to switch the storefront into maintenance mode or rewrite
    # what every printed quote says. Migration a3d81f6c94e2 backfills it to true for
    # accounts that already held all four flags above - what app/core/deps.py's module
    # docstring calls a "de-facto super admin" - so existing owners aren't locked out.
    admin = Column(Boolean, default=False, nullable=False, server_default="false")

    # --- Added: required to support password auth / email confirmation -----
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    verification_token = Column(String(255), nullable=True, unique=True, index=True)
    verification_token_expires = Column(DateTime(timezone=True), nullable=True)
    reset_token = Column(String(255), nullable=True, unique=True, index=True)
    reset_token_expires = Column(DateTime(timezone=True), nullable=True)
    last_login = Column(DateTime(timezone=True), nullable=True)


class Customer(AuditedMixin, Base):
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

    # --- Optional demographics (self-editable via PUT /customers/me) --------
    # Both nullable: every customer that existed before these columns did has
    # them empty, self-registration doesn't ask for them, and a customer is
    # never forced to fill them in. `gender` is a free String rather than a DB
    # enum for the same reason discount_type/order_type are - the allowed set
    # ("male"/"female"/"other") is enforced by the Pydantic Literal in
    # schemas.py, so widening it later needs no migration.
    date_of_birth = Column(Date, nullable=True)
    gender = Column(String(10), nullable=True)

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


class Brand(AuditedMixin, Base):
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True, index=True)
    brand_name = Column(String(150), unique=True, nullable=False, index=True)
    brand_image = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    products = relationship("Product", back_populates="brand")


class Category(AuditedMixin, Base):
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


class Product(AuditedMixin, Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    product_name = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)

    # The price actually charged, i.e. AFTER discount. This is the number that ends
    # up on an order line.
    price = Column(Numeric(10, 2), nullable=False)

    # The price before the discount - the "was $X" struck through on the storefront.
    # Stored, not derived: it used to be recovered as price/(1 - discount/100) at
    # display time, which meant it silently moved whenever `price` was edited (drop
    # the price and the "was" figure dropped with it, with nobody having touched it).
    # Kept consistent by the router, which derives it from price+discount when the
    # caller doesn't send one, and rejects a value below `price`. See migration
    # f2a9c4e18b73.
    list_price = Column(Numeric(10, 2), nullable=False)

    # Either a percentage (0-100) or a flat $ amount off list_price, per
    # discount_type. Retained alongside list_price because it's what the storefront
    # prints in the "Discount" column ("15%" reads better than making the reader
    # compare two prices), and because percent-vs-cash changes how the printed quote
    # groups the line - see printedItemDiscountText() in main.js. Numeric (not
    # Integer) so a cash discount can hold cents.
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

    # Extra photos shown in the storefront's product-page gallery, alongside (not
    # instead of) product_image - see ProductImage.
    images = relationship(
        "ProductImage",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductImage.sort_order, ProductImage.id",
    )


class ProductImage(Base):
    """One extra photo of a product, for the detail page's gallery.

    A separate table rather than more `*_image` columns on Product (the shape
    Set uses for its single detail image) because the count isn't fixed - a
    chair might be photographed from eight angles and a burr from one.

    `product_image` on Product stays the *primary* image: it's what the catalog
    card, the cart, the printed quote and the Telegram alert all show, and it's
    the first frame of the gallery. Rows here are only ever the additional ones,
    so nothing that reads `product_image` had to learn about this table.

    Not AuditedMixin: a gallery row is created and deleted, never edited, so
    "who last changed it" would only ever repeat who uploaded it."""

    __tablename__ = "product_images"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image = Column(String(500), nullable=False)

    # Display position within the gallery. Assigned by the router as
    # max(existing) + 1 on upload, so images stay in the order they were added
    # and a delete doesn't renumber the survivors.
    sort_order = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    product = relationship("Product", back_populates="images")


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


class Manual(AuditedMixin, Base):
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


class Promotion(AuditedMixin, Base):
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


class Set(AuditedMixin, Base):
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

    # Which brand the set belongs to, so the Promotions page can be browsed by
    # brand the way the catalog is. Nullable, unlike Product.brand_id: every
    # existing set predates this column, and a mixed-brand bundle legitimately
    # belongs to no single brand (those sit under "All").
    brand_id = Column(
        Integer, ForeignKey("brands.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # No back_populates onto Brand: Brand.sets is deliberately not defined, so
    # SQLAlchemy has no relationship to "helpfully" NULL out when a brand is
    # deleted and the DB's ON DELETE RESTRICT is what answers - the same
    # in-use-brand protection Product gets from brand_id being NOT NULL. See
    # the passive_deletes comment on Category.products for the failure mode.
    brand = relationship("Brand")

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


class Order(AuditedMixin, Base):
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

    # For an order that came from a customer KHQR checkout: the PendingCheckout
    # reference the QR was issued under (its bill number at the bank). order_number is
    # only assigned when the order is written, i.e. after payment, so this is the only
    # thing that ties a bank statement line back to the sale. NULL on every other row.
    payment_reference = Column(String(30), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    customer = relationship("Customer")
    # foreign_keys is required now that AuditedMixin gives `orders` a SECOND FK to
    # users (updated_by_user_id) - without it SQLAlchemy can't tell which of the two
    # this relationship means and raises AmbiguousForeignKeysError at mapper setup.
    created_by = relationship("User", foreign_keys=[created_by_user_id])
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


class PendingCheckout(Base):
    """A customer's KHQR checkout that has been priced and issued a QR but not paid yet.

    An Order is only ever written once money has actually arrived, so between "customer
    pressed Confirm Purchase" and "Bakong says it's paid" there is no order and no
    order_items - just this row. On confirmation it is materialized into a real paid
    Order (routers/orders.py::_materialize_checkout) and `order_id` is filled in;
    nothing unpaid is ever visible to a customer as an order.

    `snapshot` holds the fully-priced lines, not the cart the browser sent: the QR
    commits the customer to an exact amount, so the order that eventually gets written
    must be the one that was priced when the QR was issued, even if a product's price
    changed in between. See _snapshot_lines/_restore_lines.
    """

    __tablename__ = "pending_checkouts"

    id = Column(Integer, primary_key=True, index=True)

    # Stands in for order_number until there is an order: it's the bill number embedded
    # in the KHQR (tag 62 sub-01) and the PayWay tran_id, so it's the handle the payment
    # is identified by at the bank. Copied onto the order as payment_reference when the
    # order is finally written, which is what ties a bank statement line to a sale.
    reference = Column(String(30), unique=True, nullable=False, index=True)

    # Only customers ever reach this path - a staff cart produces a quote, never a
    # payable order. CASCADE because an abandoned checkout has no meaning without them.
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)

    # The whole order-to-be: clinic details, discount, and the priced lines. JSON rather
    # than real rows precisely because rows are what we're refusing to create.
    snapshot = Column(JSON, nullable=False)
    grand_total = Column(Numeric(10, 2), nullable=False)

    khqr_string = Column(String(512), nullable=False)
    khqr_md5 = Column(String(32), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Matches the expiry embedded in the QR itself. Past this, the QR is refused by the
    # payer's app, so an unmaterialized row is just litter - the reconciliation sweep
    # prunes it after a grace period (services/checkout_sweep.py).
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # NULL until payment is confirmed. Set (once) to the order that was written from
    # this snapshot, which is also what makes confirmation idempotent under a racing
    # browser poll and background sweep.
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)

    customer = relationship("Customer")
    order = relationship("Order")


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
    # The pre-discount unit price, snapshotted like everything else on this row -
    # the "UP before Discount" column on the printed quote reads it directly rather
    # than dividing unit_price back out (see Product.list_price). For a
    # promotion/set line it's what the bundle's contents would have cost bought
    # separately; for a $0 component line it's 0, same as unit_price.
    list_price = Column(Numeric(10, 2), nullable=False)
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


class AppSetting(Base):
    """One overridden site-wide setting, as a key/value row.

    Deliberately a key/value table rather than a wide one-row `settings` table with a
    column per setting: adding a setting is then a code change in
    app/core/settings_spec.py and nothing else, with no migration and no risk of the
    schema and the admin form drifting apart.

    Only *overrides* live here. Every setting's default is declared in the spec, and a
    key with no row simply reads as its default - which means a fresh database needs no
    seeding, and "reset to default" is a DELETE rather than a write of a value someone
    has to remember. `value` is JSON so a bool stays a bool and a number stays a number;
    reading a row for a key the spec no longer defines just ignores it.

    Secrets are NOT stored here. API keys and tokens (PayWay, Bakong, Telegram, SMTP,
    R2) stay in the environment where the deployment already keeps them - the Settings
    screen only reports whether each is configured. Two sources of truth for a
    credential is how you get a "why is it still using the old key" afternoon.
    """

    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(JSON, nullable=True)

    # Same pair as AuditedMixin, spelled out rather than mixed in: this table has no
    # `id`, and "who last changed the store's phone number" is worth keeping even
    # though nothing else about the row is.
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])


class QrCode(AuditedMixin, Base):
    """One card in the contact page's "Department QR Codes" grid.

    A table rather than more keys in app/core/settings_spec.py, which is where the
    four captions used to live (group "qr", removed in migration d3b7f1c5a92e). A
    key/value spec can only ever describe a FIXED number of fields, so "add a fifth
    department" meant a code change in two repos; and the QR pictures themselves
    weren't editable at all - they were a file drop under the Flask app's
    static/images/qr/. Both are now ordinary admin CRUD.

    Deliberately has nothing to do with the KHQR payment codes (app/services/khqr.py),
    which are generated per order and never stored - these are static pictures of a
    Telegram/WhatsApp/Facebook link for a department.
    """

    __tablename__ = "qr_codes"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(150), nullable=False)
    subtitle = Column(String(200), nullable=True)

    # Nullable so a card can be created before its picture is ready (and so an
    # upload failing doesn't lose the captions) - the storefront renders its "QR
    # coming soon" placeholder until one is uploaded. Stored like every other
    # image field: an R2 URL, or a local /static/... path. See app/core/files.py.
    qr_image = Column(String(500), nullable=True)

    # The small pill under the caption. Empty label = no pill, which is why this
    # is nullable rather than defaulted: not every department needs one.
    badge_label = Column(String(60), nullable=True)
    # Colour only - "" / machinery / materials / implants, matching the .qr-badge
    # modifier classes in the Flask app's static/css/products.css. A plain String
    # and not a DB enum so adding a colour later needs no migration (same reasoning
    # as discount_type and order_type); the allowed set is the Literal in schemas.py.
    badge_variant = Column(String(30), nullable=True)
    # Font Awesome class shown inside the pill, e.g. "fa-wrench".
    badge_icon = Column(String(60), nullable=True)

    # Display position, low to high. Not nullable (with a server_default) because
    # every card has to sort somewhere, and NULL ordering differs between backends.
    sort_order = Column(Integer, nullable=False, server_default="0")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
