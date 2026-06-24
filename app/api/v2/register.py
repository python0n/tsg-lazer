"""User registration endpoint."""

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import status
from pydantic import BaseModel
from pydantic import field_validator
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.security import get_password_hash
from app.models.user import GameMode
from app.models.user import User
from app.models.user import UserStatistics

router = APIRouter()


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        if len(v) > 32:
            raise ValueError("Username must be at most 32 characters")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class RegisterResponse(BaseModel):
    id: int
    username: str
    email: str


@router.post(
    "/users",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Registration"],
)
async def register(db: DbSession, data: RegisterRequest) -> RegisterResponse:
    """Register a new user account."""

    # Check username taken
    result = await db.execute(select(User).where(User.username == data.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"form": {"username": ["Username is already taken"]}},
        )

    # Check email taken
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"form": {"user_email": ["Email is already taken"]}},
        )

    # Create user
    user = User(
        username=data.username,
        email=data.email,
        password_hash=get_password_hash(data.password),
    )
    db.add(user)
    await db.flush()  # get user.id

    # Create statistics for all 4 modes
    for mode in GameMode:
        db.add(UserStatistics(user_id=user.id, mode=mode))

    await db.commit()

    return RegisterResponse(id=user.id, username=user.username, email=user.email)
