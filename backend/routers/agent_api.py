"""Agent-only API routes (registration, heartbeat)."""
from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.agent import Agent, AgentStatus
from models.skill import Skill
from models.agent_skill import AgentSkill
from schemas import (
    AgentRegistrationResponse, 
    AgentHeartbeatResponse,
    AgentCreate,
    HealthCheckResponse
)
from auth import get_password_hash
from services.health_checker import generate_health_check_token

router = APIRouter(prefix="/api/agent", tags=["agent-api"])


async def get_agent_from_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db)
) -> Agent:
    """Dependency to get agent from API key header."""
    from sqlalchemy import select
    
    # Hash the provided key and look it up
    # Actually, we need to check all agents - inefficient but works for POC
    # In production, use a faster lookup method
    result = await db.execute(select(Agent))
    agents = result.scalars().all()
    
    for agent in agents:
        # Verify password-style hash
        from auth import verify_password
        if verify_password(x_api_key, agent.api_key_hash):
            return agent
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key"
    )


@router.post("/register", response_model=AgentRegistrationResponse)
async def register_agent(
    agent_data: AgentCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    Register a new agent.
    This is called by the agent itself or the deployment service.
    Returns the FULL API key - save it immediately! It won't be shown again.
    """
    # Generate API key
    import secrets
    api_key = f"am-{secrets.token_urlsafe(32)}"
    api_key_hash = get_password_hash(api_key)
    
    # Generate health check token
    health_check_token = await generate_health_check_token()
    
    # Create agent record
    agent = Agent(
        name=agent_data.name,
        description=agent_data.description,
        api_key_hash=api_key_hash,
        status=AgentStatus.PENDING.value,
        health_check_token=health_check_token,
        version="1.0.0"
    )
    
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    
    # Add skills if provided
    if agent_data.skill_ids:
        for skill_id in agent_data.skill_ids:
            result = await db.execute(select(Skill).where(Skill.id == skill_id))
            skill = result.scalar_one_or_none()
            if skill:
                config = agent_data.skill_configs.get(skill_id, {}) if agent_data.skill_configs else {}
                agent_skill = AgentSkill(
                    agent_id=agent.id,
                    skill_id=skill_id,
                    config=config
                )
                db.add(agent_skill)
        
        await db.commit()
    
    # Generate endpoint URL
    endpoint_url = f"/agents/{agent.id}/invoke"
    agent.endpoint_url = endpoint_url
    await db.commit()
    
    # Log the full API key for server-side recovery if needed
    print(f"🔑 Agent registered: {agent.name} (ID: {agent.id})")
    print(f"🔑 API Key (SAVE THIS!): {api_key}")
    
    # Return response with explicit full API key
    response_data = {
        "agent_id": agent.id,
        "api_key": api_key,  # Full key - only returned once!
        "health_check_endpoint": f"/agents/{agent.id}/health",
        "health_check_token": health_check_token,
        "status": agent.status
    }
    
    return response_data


@router.post("/heartbeat", response_model=AgentHeartbeatResponse)
async def agent_heartbeat(
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Agent heartbeat - updates last_seen timestamp."""
    agent.last_seen = datetime.utcnow()
    agent.status = AgentStatus.ACTIVE.value
    await db.commit()
    
    return AgentHeartbeatResponse(
        status="active",
        message="Heartbeat received"
    )


@router.get("/me")
async def get_agent_profile(
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Get current agent's profile."""
    # Load skills
    result = await db.execute(
        select(AgentSkill)
        .where(AgentSkill.agent_id == agent.id)
        .join(Skill)
    )
    agent_skills = result.scalars().all()
    
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "status": agent.status,
        "endpoint_url": agent.endpoint_url,
        "skills": [
            {
                "id": askill.skill.id,
                "name": askill.skill.name,
                "display_name": askill.skill.display_name
            }
            for askill in agent_skills
        ]
    }


@router.put("/me")
async def update_agent_profile(
    agent_update: dict,
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Update current agent's profile."""
    if "name" in agent_update:
        agent.name = agent_update["name"]
    if "description" in agent_update:
        agent.description = agent_update["description"]
    
    await db.commit()
    await db.refresh(agent)
    
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description
    }


@router.post("/recover-credentials")
async def recover_credentials(
    agent_id: str,
    health_check_token: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Recover agent credentials using health check token.
    This is a one-time recovery - generates a NEW API key.
    """
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    if agent.health_check_token != health_check_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid health check token"
        )
    
    # Generate new API key (old one is lost forever)
    import secrets
    new_api_key = f"am-{secrets.token_urlsafe(32)}"
    agent.api_key_hash = get_password_hash(new_api_key)
    
    # Generate new health check token too (for security)
    new_health_token = await generate_health_check_token()
    agent.health_check_token = new_health_token
    
    await db.commit()
    
    print(f"🔄 Credentials recovered for agent: {agent.name} (ID: {agent.id})")
    print(f"🔑 New API Key: {new_api_key}")
    
    return {
        "agent_id": agent.id,
        "api_key": new_api_key,
        "health_check_token": new_health_token,
        "message": "New credentials generated. Save these immediately - they won't be shown again!"
    }
