from fastapi import APIRouter, status, HTTPException, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.api.models import User, Category, SubCategory, SubCategoryTeam
from app.api.db import get_session
from typing import Annotated
from app.api.v1.deps import get_current_active_user

router = APIRouter(
    prefix="/subcategories",
    tags=["sub-categories"]
)

@router.get("/", response_model=list[SubCategory], status_code=status.HTTP_200_OK)
async def get_categories(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ):
    """Get all categories."""
    result = await session.exec(select(SubCategory))
    categories = result.all()
    return categories

@router.post("/", response_model=SubCategory, status_code=status.HTTP_201_CREATED)
async def create_category(sub_category: SubCategory, session: Annotated[AsyncSession, Depends(get_session)], current_user: Annotated[User, Depends(get_current_active_user)]):
    """Create a new category."""
    session.add(sub_category)
    await session.commit()
    await session.refresh(sub_category)
    return sub_category


@router.get("/{subcategory_id}", response_model=SubCategory, status_code=status.HTTP_200_OK)
async def get_subcategory(
    subcategory_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Get a single subcategory by ID."""
    result = await session.exec(select(SubCategory).where(SubCategory.id == subcategory_id))
    subcategory = result.first()
    if not subcategory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subcategory not found")
    return subcategory


@router.put("/{subcategory_id}", response_model=SubCategory, status_code=status.HTTP_200_OK)
async def update_subcategory(
    subcategory_id: int,
    subcategory_data: SubCategory,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Update a subcategory by ID."""
    result = await session.exec(select(SubCategory).where(SubCategory.id == subcategory_id))
    subcategory = result.first()
    if not subcategory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subcategory not found")
    subcategory.subcategory_in_english = subcategory_data.subcategory_in_english
    subcategory.subcategory_in_bangla = subcategory_data.subcategory_in_bangla
    from datetime import datetime
    subcategory.updated_at = datetime.utcnow()
    session.add(subcategory)
    await session.commit()
    await session.refresh(subcategory)
    return subcategory


@router.delete("/{subcategory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subcategory(
    subcategory_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Delete a subcategory by ID."""
    result = await session.exec(select(SubCategory).where(SubCategory.id == subcategory_id))
    subcategory = result.first()
    if not subcategory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subcategory not found")
    await session.delete(subcategory)
    await session.commit()
