import math

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.models.database import get_db
from app.models.user import User
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.services.token_service import wit_now
from app.services.auth_service import (
    create_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ---------- Schemas ----------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=255)
    profession: str = Field(default="umum", max_length=100)
    organization: str = Field(default="", max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserInfo"


class UserInfo(BaseModel):
    id: str
    email: str
    full_name: str
    profession: str
    organization: str
    role: str = "user"
    status: str = "pending"
    package_code: str = "starter"
    package_name: str = "Starter"
    package_expires_at: str | None = None
    package_days_remaining: int | None = None
    package_is_default: bool = True

    class Config:
        from_attributes = True


class MessageResponse(BaseModel):
    message: str




def build_user_info(db: Session, user: User) -> UserInfo:
    """Profil pengguna beserta paket efektif berdasarkan masa berlaku WIT."""
    data = UserInfo.model_validate(user).model_dump()

    now = wit_now()
    active = (
        db.query(UserSubscription, SubscriptionPlan)
        .join(
            SubscriptionPlan,
            SubscriptionPlan.id == UserSubscription.plan_id,
        )
        .filter(
            UserSubscription.user_id == user.id,
            UserSubscription.status == "active",
            UserSubscription.current_period_start <= now,
            UserSubscription.current_period_end > now,
            SubscriptionPlan.is_active.is_(True),
        )
        .order_by(UserSubscription.current_period_start.desc())
        .first()
    )

    if active is None:
        data.update({
            "package_code": "starter",
            "package_name": "Starter",
            "package_expires_at": None,
            "package_days_remaining": None,
            "package_is_default": True,
        })
    else:
        subscription, plan = active
        seconds_left = (
            subscription.current_period_end - now
        ).total_seconds()
        days_left = max(1, math.ceil(seconds_left / 86400))

        data.update({
            "package_code": plan.code,
            "package_name": plan.name,
            "package_expires_at": subscription.current_period_end.isoformat(),
            "package_days_remaining": days_left,
            "package_is_default": False,
        })

    return UserInfo(**data)

# ---------- Endpoints ----------


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    # Cek email sudah terdaftar
    existing = (
        db.query(User).filter(User.email == data.email.lower()).first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email sudah terdaftar. Silakan login.",
        )

    user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        profession=data.profession,
        organization=data.organization,
        status="pending",  # menunggu persetujuan admin
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Token tetap diterbitkan agar user bisa cek status via /auth/me,
    # TAPI chat diblokir selama status != active.
    token = create_access_token({"sub": user.id})
    return TokenResponse(access_token=token, user=build_user_info(db, user))


@router.post("/login", response_model=TokenResponse)
def login(
    form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == form.username.lower()).first()
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email atau password salah.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.status == "pending":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Akun Anda masih MENUNGGU PERSETUJUAN admin. "
                "Silakan coba lagi nanti setelah disetujui."
            ),
        )
    if user.status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pendaftaran Anda DITOLAK admin. Hubungi admin untuk info lebih lanjut.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun dinonaktifkan.",
        )

    token = create_access_token({"sub": user.id})
    return TokenResponse(access_token=token, user=build_user_info(db, user))


@router.get("/me", response_model=UserInfo)
def me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return build_user_info(db, current_user)
