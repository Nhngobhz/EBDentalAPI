from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import get_verified_customer, require_permission
from app.core.email import send_customer_verification_email
from app.core.files import save_image
from app.core.security import generate_url_safe_token, hash_password, verify_password
from app.database import get_db
from app.models import Customer, User
from app.schemas import (
    ChangePassword,
    CustomerCreate,
    CustomerOut,
    CustomerSelfUpdate,
    CustomerUpdate,
    Message,
)

router = APIRouter(prefix="/customers", tags=["Customers"])

_perm = Depends(require_permission("customer_management"))


@router.get("/me", response_model=CustomerOut)
def read_my_profile(current_customer: Customer = Depends(get_verified_customer)):
    return current_customer


@router.put("/me", response_model=CustomerOut)
def update_my_profile(
    payload: CustomerSelfUpdate,
    background_tasks: BackgroundTasks,
    current_customer: Customer = Depends(get_verified_customer),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude_unset=True)

    new_email = data.pop("email", None)
    if new_email and new_email != current_customer.email:
        if db.query(Customer).filter(Customer.email == new_email, Customer.id != current_customer.id).first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")
        current_customer.email = new_email
        current_customer.is_verified = False
        token = generate_url_safe_token()
        current_customer.verification_token = token
        current_customer.verification_token_expires = datetime.now(timezone.utc) + timedelta(
            minutes=settings.EMAIL_VERIFICATION_EXPIRE_MINUTES
        )
        background_tasks.add_task(send_customer_verification_email, current_customer.email, token)

    for field, value in data.items():
        setattr(current_customer, field, value)
    db.commit()
    db.refresh(current_customer)
    return current_customer


@router.post("/me/change-password", response_model=Message)
def change_my_password(
    payload: ChangePassword,
    current_customer: Customer = Depends(get_verified_customer),
    db: Session = Depends(get_db),
):
    if not current_customer.hashed_password or not verify_password(
        payload.current_password, current_customer.hashed_password
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    current_customer.hashed_password = hash_password(payload.new_password)
    db.commit()
    return {"detail": "Password updated successfully"}


@router.post("/me/image", response_model=CustomerOut)
async def upload_my_image(
    file: UploadFile,
    current_customer: Customer = Depends(get_verified_customer),
    db: Session = Depends(get_db),
):
    current_customer.customer_image = await save_image(file, "customers")
    db.commit()
    db.refresh(current_customer)
    return current_customer


@router.get("/", response_model=list[CustomerOut])
def list_customers(
    skip: int = 0,
    limit: int = 50,
    q: str | None = None,
    _: User = _perm,
    db: Session = Depends(get_db),
):
    query = db.query(Customer)
    if q:
        query = query.filter(
            or_(Customer.customer_name.ilike(f"%{q}%"), Customer.email.ilike(f"%{q}%"))
        )
    return query.order_by(Customer.id).offset(skip).limit(limit).all()


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: int, _: User = _perm, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    return customer


@router.post("/", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(payload: CustomerCreate, _: User = _perm, db: Session = Depends(get_db)):
    if db.query(Customer).filter(Customer.email == payload.email).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")
    customer = Customer(**payload.model_dump())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.put("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    _: User = _perm,
    db: Session = Depends(get_db),
):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)
    db.commit()
    db.refresh(customer)
    return customer


@router.post("/{customer_id}/image", response_model=CustomerOut)
async def upload_customer_image(
    customer_id: int, file: UploadFile, _: User = _perm, db: Session = Depends(get_db)
):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    customer.customer_image = await save_image(file, "customers")
    db.commit()
    db.refresh(customer)
    return customer


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(customer_id: int, _: User = _perm, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    db.delete(customer)
    db.commit()
    return None
