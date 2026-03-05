from sqlmodel import SQLModel, Field
from pydantic import EmailStr
import uuid
from datetime import datetime
from typing import Optional


class UserBase(SQLModel):
    username: str = Field(default=None, index=True, max_length=50)
    email: EmailStr = Field(default=None, index=True, max_length=100)
    full_name: str | None = Field(default=None, max_length=255)


class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    hashed_password: str = Field(default=None, max_length=256)


class UserAdminDisplay(UserBase):
    id: uuid.UUID
    is_active: bool
    is_superuser: bool


class UserPublic(UserBase):
    id: uuid.UUID


class UserLogin(SQLModel):
    username: str = Field(default=None, max_length=50)
    password: str = Field(default=None, max_length=256)


class UserCreate(UserBase):
    password: str = Field(default=None, max_length=256)


class UserStatusUpdate(SQLModel):
    is_active: bool


class TokenResponse(SQLModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefresh(SQLModel):
    refresh_token: str


class TokenBlacklist(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    token: str = Field(index=True, unique=True)


class TicketBase(SQLModel):
    category: str = Field(default=None, max_length=100)
    subcategory: str = Field(default=None, max_length=100)
    assigned_team: str = Field(default=None, max_length=100)
    priority: str = Field(default="Low", max_length=50)
    status: str = Field(default="Open", max_length=50)
    ref_ticket_id: Optional[uuid.UUID] = Field(default=None, index=True)
    description: Optional[str] = Field(default=None)


class Ticket(TicketBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TicketPublic(TicketBase):
    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    category_in_english: str = Field(default=None, max_length=255)
    category_in_bangla: str = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SubCategory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    subcategory_in_english: str = Field(default=None, max_length=255)
    subcategory_in_bangla: str = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SubCategoryTeam(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    category_id: int = Field(default=None, index=True)
    sub_category_id: int = Field(default=None, index=True)
    team_id: int = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)