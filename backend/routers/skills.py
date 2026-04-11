"""Skill catalog routes."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.skill import Skill
from schemas import SkillResponse, SkillCreate
from services.skill_catalog import get_all_skills
from auth import get_current_active_user, get_current_admin_user
from models.user import User

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("", response_model=List[SkillResponse])
async def list_skills(
    tier: str = None,
    db: AsyncSession = Depends(get_db)
):
    """List all available skills, optionally filtered by tier."""
    skills = await get_all_skills(db, tier=tier)
    return skills


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: str, db: AsyncSession = Depends(get_db)):
    """Get details of a specific skill."""
    from sqlalchemy import select
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found"
        )
    
    return skill


@router.post("", response_model=SkillResponse)
async def create_skill(
    skill_data: SkillCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """Create a new skill (admin only)."""
    
    from sqlalchemy import select
    # Check if skill name already exists
    result = await db.execute(select(Skill).where(Skill.name == skill_data.name))
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Skill with name '{skill_data.name}' already exists"
        )
    
    skill = Skill(**skill_data.model_dump())
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    
    return skill
