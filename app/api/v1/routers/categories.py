from fastapi import APIRouter, status, HTTPException, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.api.models import User, Category, SubCategory, SubCategoryTeam
from app.api.db import get_session
from typing import Annotated
from app.api.v1.deps import get_current_active_user

router = APIRouter(
    prefix="/categories",
    tags=["categories"]
)

@router.get("/", response_model=list[Category], status_code=status.HTTP_200_OK)
async def get_categories(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ):
    """Get all categories."""
    result = await session.exec(select(Category))
    categories = result.all()
    return categories

@router.post("/", response_model=Category, status_code=status.HTTP_201_CREATED)
async def create_category(category: Category, session: Annotated[AsyncSession, Depends(get_session)], current_user: Annotated[User, Depends(get_current_active_user)]):
    """Create a new category."""
    session.add(category)
    await session.commit()
    await session.refresh(category)
    return category

@router.get("/subcategories", status_code=status.HTTP_200_OK)
async def get_teams_for_category(session: Annotated[AsyncSession, Depends(get_session)]):
    """Get subcategories grouped by category."""
    result = await session.exec(
        select(Category.category_in_english, SubCategory.subcategory_in_english)
        .join(SubCategoryTeam, SubCategoryTeam.category_id == Category.id)
        .join(SubCategory, SubCategoryTeam.sub_category_id == SubCategory.id)
        .distinct()
    )
    rows = result.all()

    grouped: dict[str, list[str]] = {}
    for category_name, subcategory_name in rows:
        grouped.setdefault(category_name, [])
        if subcategory_name not in grouped[category_name]:
            grouped[category_name].append(subcategory_name)

    return grouped