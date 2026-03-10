from fastapi import APIRouter, status, HTTPException, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.api.models import User, Category, SubCategory, SubCategoryTeam
from app.api.db import get_session
from typing import Annotated
from app.api.v1.deps import get_current_active_user
from datetime import datetime, timezone

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

@router.get("/{category_id}/subcategories", status_code=status.HTTP_200_OK)
async def get_subcategories_for_category(
    category_id: int,
    session: Annotated[AsyncSession, Depends(get_session)]
):
    """Get subcategories for a category."""
    result = await session.exec(select(SubCategoryTeam).where(SubCategoryTeam.category_id == category_id))
    
    subcategory_teams = result.all()
    subcategories = {}
    
    for subcategory_team in subcategory_teams:
        subcategory_result = await session.exec(select(SubCategory).where(SubCategory.id == subcategory_team.sub_category_id))
        subcategory = subcategory_result.first()
        if subcategory:
            subcategories[subcategory.id] = subcategory

    return subcategories


@router.get("/{category_id}", response_model=Category, status_code=status.HTTP_200_OK)
async def get_category(
    category_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Get a single category by ID."""
    result = await session.exec(select(Category).where(Category.id == category_id))
    category = result.first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    return category


@router.put("/{category_id}", response_model=Category, status_code=status.HTTP_200_OK)
async def update_category(
    category_id: int,
    category_data: Category,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Update a category by ID."""
    result = await session.exec(select(Category).where(Category.id == category_id))
    category = result.first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    category.category_in_english = category_data.category_in_english
    category.category_in_bangla = category_data.category_in_bangla
    category.updated_at = datetime.now(timezone.utc)
    session.add(category)
    await session.commit()
    await session.refresh(category)
    return category


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Delete a category by ID."""
    result = await session.exec(select(Category).where(Category.id == category_id))
    category = result.first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    await session.delete(category)
    await session.commit()


@router.post("/{category_id}/subcategories/{subcategory_id}", status_code=status.HTTP_201_CREATED)
async def add_subcategory_to_category(
    category_id: int,
    subcategory_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_active_user)]
):
    """Add a subcategory to a category."""
    # Check if the category exists
    category_result = await session.exec(select(Category).where(Category.id == category_id))
    category = category_result.first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    # Check if the subcategory exists
    subcategory_result = await session.exec(select(SubCategory).where(SubCategory.id == subcategory_id))
    subcategory = subcategory_result.first()
    if not subcategory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subcategory not found")

    # Create the association
    association = SubCategoryTeam(category_id=category_id, sub_category_id=subcategory_id)
    session.add(association)
    await session.commit()
    return {"message": "Subcategory added to category successfully"}