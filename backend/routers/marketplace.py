"""Marketplace routes for public agent discovery."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from database import get_db
from models.agent import Agent, AgentStatus
from models.agent_review import AgentReview
from models.agent_skill import AgentSkill
from schemas import MarketplaceAgentCard

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])


@router.get("/agents")
async def list_marketplace_agents(
    skill: Optional[str] = None,
    max_cost: Optional[float] = None,
    min_rating: Optional[float] = None,
    tags: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = Query("rating", regex="^(rating|recent|name)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Browse public agents in the marketplace.
    
    Filters:
    - skill: Filter by skill name
    - max_cost: Maximum token cost per task
    - min_rating: Minimum average rating
    - tags: Comma-separated tags
    - search: Search in name and description
    - sort: Sort by rating, recent, or name
    """
    query = select(Agent).where(
        Agent.is_public == True,
        Agent.status.in_([
            AgentStatus.ACTIVE.value,
            AgentStatus.IDLE.value
        ])
    ).options(selectinload(Agent.skills))
    
    # Apply filters
    if search:
        search_filter = f"%{search}%"
        query = query.where(
            (Agent.name.ilike(search_filter)) |
            (Agent.marketplace_description.ilike(search_filter)) |
            (Agent.description.ilike(search_filter))
        )
    
    if tags:
        tag_list = [t.strip() for t in tags.split(",")]
        # SQLite JSON filtering is limited, filter in Python after fetch
    
    if skill:
        query = query.join(AgentSkill).join(AgentSkill.skill).where(
            AgentSkill.skill.has(name=skill)
        )
    
    # Get total count
    count_query = select(func.count()).select_from(query.alias())
    total_result = await db.execute(count_query)
    total_count = total_result.scalar()
    
    # Sorting
    if sort == "recent":
        query = query.order_by(Agent.last_seen.desc().nullslast())
    elif sort == "name":
        query = query.order_by(Agent.name.asc())
    else:  # rating
        query = query.order_by(Agent.created_at.desc())  # TODO: Add rating sort
    
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    agents = result.scalars().unique().all()
    
    # Enrich with ratings
    agent_cards = []
    for agent in agents:
        # Calculate average rating
        rating_result = await db.execute(
            select(func.avg(AgentReview.rating), func.count(AgentReview.id))
            .where(AgentReview.agent_id == agent.id)
        )
        avg_rating, review_count = rating_result.one()
        
        # Filter by pricing if specified
        if max_cost and agent.pricing_model:
            if agent.pricing_model.get("type") == "token":
                rate = agent.pricing_model.get("rate", 0)
                if rate > max_cost:
                    continue
        
        # Filter by rating if specified
        if min_rating and avg_rating and avg_rating < min_rating:
            continue
        
        agent_cards.append({
            "id": agent.id,
            "name": agent.name,
            "slug": agent.slug,
            "avatar_url": agent.avatar_url,
            "marketplace_description": agent.marketplace_description or agent.description,
            "pricing_model": agent.pricing_model,
            "tags": agent.tags or [],
            "status": agent.status,
            "owner_id": agent.owner_id,
            "last_seen": agent.last_seen,
            "average_rating": float(avg_rating) if avg_rating else None,
            "total_reviews": review_count or 0
        })
    
    return {
        "items": agent_cards,
        "total": total_count,
        "limit": limit,
        "offset": offset
    }


@router.get("/agents/{agent_id}")
async def get_marketplace_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Get detailed information about a public agent."""
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
    
    if not agent.is_public:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent is not public"
        )
    
    # Get reviews
    reviews_result = await db.execute(
        select(AgentReview)
        .where(AgentReview.agent_id == agent_id)
        .order_by(AgentReview.created_at.desc())
        .limit(10)
    )
    reviews = reviews_result.scalars().all()
    
    # Calculate stats
    rating_result = await db.execute(
        select(func.avg(AgentReview.rating), func.count(AgentReview.id))
        .where(AgentReview.agent_id == agent_id)
    )
    avg_rating, review_count = rating_result.one()
    
    return {
        "id": agent.id,
        "name": agent.name,
        "slug": agent.slug,
        "avatar_url": agent.avatar_url,
        "description": agent.description,
        "marketplace_description": agent.marketplace_description,
        "pricing_model": agent.pricing_model,
        "tags": agent.tags or [],
        "capabilities": agent.capabilities or [],
        "status": agent.status,
        "last_seen": agent.last_seen,
        "owner_id": agent.owner_id,
        "skills": [
            {
                "id": askill.skill.id,
                "name": askill.skill.name,
                "display_name": askill.skill.display_name,
                "category": askill.skill.category
            }
            for askill in agent.skills
        ],
        "average_rating": float(avg_rating) if avg_rating else None,
        "total_reviews": review_count or 0,
        "recent_reviews": [
            {
                "rating": review.rating,
                "comment": review.comment,
                "created_at": review.created_at
            }
            for review in reviews
        ]
    }


@router.get("/categories")
async def get_marketplace_categories(db: AsyncSession = Depends(get_db)):
    """Get skill categories for filtering."""
    from models.skill import Skill
    
    result = await db.execute(
        select(Skill.category, func.count(Skill.id))
        .group_by(Skill.category)
    )
    categories = result.all()
    
    return [
        {"name": cat, "count": count}
        for cat, count in categories
    ]
