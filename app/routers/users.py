from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import get_current_user, get_verified_user, require_permission
from app.core.email import send_verification_email
from app.core.files import save_image
from app.core.security import generate_url_safe_token, hash_password, verify_password
from app.database import get_db
from app.models import User
from app.schemas import (
    ChangePassword,
    Message,
    UserCreateByAdmin,
    UserOut,
    UserUpdateByAdmin,
    UserUpdateSelf,
)

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserOut)
def read_my_profile(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/me", response_model=UserOut)
def update_my_profile(
    payload: UserUpdateSelf,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_verified_user),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude_unset=True)

    new_email = data.pop("email", None)
    if new_email and new_email != current_user.email:
        if db.query(User).filter(User.email == new_email, User.id != current_user.id).first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
        current_user.email = new_email
        current_user.is_verified = False
        token = generate_url_safe_token()
        current_user.verification_token = token
        current_user.verification_token_expires = datetime.now(timezone.utc) + timedelta(
            minutes=settings.EMAIL_VERIFICATION_EXPIRE_MINUTES
        )
        background_tasks.add_task(send_verification_email, current_user.email, token)

    for field, value in data.items():
        setattr(current_user, field, value)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/change-password", response_model=Message)
def change_my_password(
    payload: ChangePassword,
    current_user: User = Depends(get_verified_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    current_user.hashed_password = hash_password(payload.new_password)
    db.commit()
    return {"detail": "Password updated successfully"}


@router.post("/me/image", response_model=UserOut)
async def upload_my_image(
    file: UploadFile,
    current_user: User = Depends(get_verified_user),
    db: Session = Depends(get_db),
):
    current_user.user_image = await save_image(file, "users")
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/", response_model=list[UserOut])
def list_users(
    skip: int = 0,
    limit: int = 50,
    _: User = Depends(require_permission("user_management")),
    db: Session = Depends(get_db),
):
    return db.query(User).order_by(User.id).offset(skip).limit(limit).all()


@router.get("/{user_id}", response_model=UserOut)
def get_user(
    user_id: int,
    _: User = Depends(require_permission("user_management")),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("/", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateByAdmin,
    background_tasks: BackgroundTasks,
    _: User = Depends(require_permission("user_management")),
    db: Session = Depends(get_db),
):
    """An admin creates a staff account with role/permissions already set.
    The new account still has to confirm its email before it can log in."""
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    user = User(
        user_name=payload.user_name,
        email=payload.email,
        address=payload.address,
        phone_num=payload.phone_num,
        hashed_password=hash_password(payload.password),
        role_title=payload.role_title,
        user_management=payload.user_management,
        price_listing=payload.price_listing,
        product_management=payload.product_management,
        customer_management=payload.customer_management,
        is_active=True,
        is_verified=False,
    )
    import secrets

    token = secrets.token_urlsafe(32)
    user.verification_token = token
    user.verification_token_expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.EMAIL_VERIFICATION_EXPIRE_MINUTES
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    background_tasks.add_task(send_verification_email, user.email, token)
    return user


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdateByAdmin,
    current_user: User = Depends(require_permission("user_management")),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    data = payload.model_dump(exclude_unset=True)

    # Safety guard: an admin cannot strip their OWN user_management
    # permission (this would either lock them out or, if they are the
    # only admin, lock everyone out of user management entirely).
    if user.id == current_user.id and data.get("user_management") is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot revoke your own user_management permission",
        )

    for field, value in data.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", response_model=UserOut)
def deactivate_user(
    user_id: int,
    current_user: User = Depends(require_permission("user_management")),
    db: Session = Depends(get_db),
):
    """Soft-delete: sets is_active=False rather than removing the row, so
    login history / audit trail is preserved."""
    if user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot deactivate your own account")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.is_active = False
    db.commit()
    db.refresh(user)
    return user
