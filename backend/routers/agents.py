"""Public agent routes (browsing, discovery)."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from database import get_db
from models.agent import Agent, AgentStatus
from models.skill import Skill
from models.agent_skill import AgentSkill
from schemas import AgentResponse, AgentDetailResponse, AgentFilter
from auth import get_current_active_user, get_current_user
from models.user import User

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/stats/overview")
async def get_agent_stats(db: AsyncSession = Depends(get_db)):
    """Get marketplace statistics."""
    # Total agents
    total_result = await db.execute(select(func.count(Agent.id)))
    total = total_result.scalar()
    
    # Active agents (online or idle)
    active_result = await db.execute(
        select(func.count(Agent.id))
        .where(Agent.status.in_([AgentStatus.ACTIVE.value, AgentStatus.IDLE.value]))
    )
    active = active_result.scalar()
    
    return {
        "total_agents": total,
        "active_agents": active,
        "offline_agents": total - active
    }


@router.get("")
async def list_agents(
    status: Optional[str] = None,
    skill_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """List all agents with optional filtering."""
    query = select(Agent).options(selectinload(Agent.skills).selectinload(AgentSkill.skill))
    
    # Apply filters
    if status:
        query = query.where(Agent.status == status)
    else:
        # Default: exclude pending/error agents from public view
        query = query.where(Agent.status.in_([
            AgentStatus.ACTIVE.value,
            AgentStatus.IDLE.value,
            AgentStatus.OFFLINE.value
        ]))
    
    if owner_id:
        query = query.where(Agent.owner_id == owner_id)
    
    if search:
        search_filter = f"%{search}%"
        query = query.where(
            (Agent.name.ilike(search_filter)) | 
            (Agent.description.ilike(search_filter))
        )
    
    if skill_id:
        # Join with AgentSkill to filter by skill
        query = query.join(AgentSkill).where(AgentSkill.skill_id == skill_id)
    
    # Get total count before pagination
    count_query = select(func.count()).select_from(query.alias())
    total_result = await db.execute(count_query)
    total_count = total_result.scalar()
    
    # Order by most recently active
    query = query.order_by(Agent.last_seen.desc().nullslast())
    query = query.limit(limit).offset(offset)
    
    result = await db.execute(query)
    agents = result.scalars().unique().all()
    
    return {
        "items": [AgentResponse.model_validate(agent) for agent in agents],
        "total": total_count,
        "limit": limit,
        "offset": offset
    }


@router.get("/{agent_id}", response_model=AgentDetailResponse)
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Get detailed information about a specific agent."""
    result = await db.execute(
        select(Agent)
        .options(selectinload(Agent.skills).selectinload(AgentSkill.skill))
        .where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    # Update calculated status
    agent.status = agent.calculate_status().value
    
    return agent


@router.get("/{agent_id}/skills", response_model=List[dict])
async def get_agent_skills(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Get skills for a specific agent."""
    result = await db.execute(
        select(AgentSkill)
        .options(selectinload(AgentSkill.skill))
        .where(AgentSkill.agent_id == agent_id)
    )
    agent_skills = result.scalars().all()
    
    return [
        {
            "id": askill.skill.id,
            "name": askill.skill.name,
            "display_name": askill.skill.display_name,
            "description": askill.skill.description,
            "tier": askill.skill.tier,
            "category": askill.skill.category
        }
        for askill in agent_skills
    ]
