"""Agent deployment routes for users."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import get_db
from models.agent import Agent, AgentStatus
from models.skill import Skill
from models.agent_skill import AgentSkill
from models.user import User
from schemas import AgentResponse, AgentCreate
from auth import get_current_active_user
from services.container_manager import create_container, delete_container, get_container_logs
from services.health_checker import perform_endpoint_challenge
from services.skill_catalog import validate_skill_selection
from cryptography.fernet import Fernet
import os
import json

router = APIRouter(prefix="/api", tags=["deploy"])

# Encryption key for API keys (should be loaded from env in production)
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", Fernet.generate_key())
fernet = Fernet(ENCRYPTION_KEY)


def decrypt_api_keys(encrypted_data: str) -> dict:
    """Decrypt user's model API keys."""
    if not encrypted_data:
        return {}
    try:
        decrypted = fernet.decrypt(encrypted_data.encode())
        return json.loads(decrypted)
    except Exception:
        return {}


@router.post("/agents/deploy", response_model=AgentResponse)
async def deploy_agent(
    agent_data: AgentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Deploy a new agent with selected skills.
    User's model API keys are injected into the container.
    """
    # Validate skill selection
    user_api_keys = decrypt_api_keys(current_user.model_api_keys_encrypted or "")
    
    is_valid, error_msg = await validate_skill_selection(
        db, agent_data.skill_ids, user_api_keys
    )
    
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg
        )
    
    # Get skill details for container
    skills = []
    for skill_id in agent_data.skill_ids:
        result = await db.execute(select(Skill).where(Skill.id == skill_id))
        skill = result.scalar_one_or_none()
        if skill:
            skills.append({
                "id": skill.id,
                "name": skill.name,
                "tier": skill.tier
            })
    
    # Create agent record first (to get ID)
    import secrets
    from auth import get_password_hash
    
    api_key = f"am-{secrets.token_urlsafe(32)}"
    api_key_hash = get_password_hash(api_key)
    
    agent = Agent(
        name=agent_data.name,
        description=agent_data.description,
        owner_id=current_user.id,
        api_key_hash=api_key_hash,
        status=AgentStatus.PENDING.value,
        version="1.0.0"
    )
    
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    
    try:
        # Create container
        container_id, port = create_container(
            agent_id=agent.id,
            agent_name=agent.name,
            skills=skills,
            env_vars=user_api_keys,
            api_key=api_key
        )
        
        # Update agent with container info
        agent.container_id = container_id
        agent.internal_port = port
        agent.endpoint_url = f"/agents/{agent.id}/invoke"
        agent.status = AgentStatus.VERIFYING.value
        await db.commit()
        
        # Add skills to agent
        for skill_id in agent_data.skill_ids:
            config = agent_data.skill_configs.get(skill_id, {}) if agent_data.skill_configs else {}
            agent_skill = AgentSkill(
                agent_id=agent.id,
                skill_id=skill_id,
                config=config
            )
            db.add(agent_skill)
        
        await db.commit()
        
        # Trigger endpoint challenge (async - don't wait)
        import asyncio
        asyncio.create_task(perform_endpoint_challenge(db, agent.id))
        
        return agent
    
    except Exception as e:
        # Cleanup on failure
        agent.status = AgentStatus.ERROR.value
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to deploy agent: {str(e)}"
        )


@router.delete("/agents/{agent_id}")
async def delete_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete an agent (owner only)."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    # Check ownership
    if agent.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this agent"
        )
    
    # Stop and delete container
    if agent.container_id:
        delete_container(agent.container_id)
    
    # Delete from database
    await db.delete(agent)
    await db.commit()
    
    return {"message": "Agent deleted successfully"}


@router.post("/agents/{agent_id}/restart")
async def restart_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Restart an agent container (owner only)."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    if agent.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized"
        )
    
    if agent.container_id:
        from services.container_manager import start_container
        success = start_container(agent.container_id)
        if success:
            agent.status = AgentStatus.PENDING.value
            await db.commit()
            
            # Trigger new challenge
            import asyncio
            asyncio.create_task(perform_endpoint_challenge(db, agent.id))
            
            return {"message": "Agent restart initiated"}
    
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to restart agent"
    )


@router.get("/agents/{agent_id}/logs")
async def get_agent_logs(
    agent_id: str,
    tail: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get agent container logs (owner only)."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    if agent.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized"
        )
    
    if agent.container_id:
        logs = get_container_logs(agent.container_id, tail)
        return {"logs": logs}
    
    return {"logs": "No container found"}


@router.patch("/me/keys")
async def update_model_api_keys(
    keys: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update user's model API keys (encrypted)."""
    # Validate keys format
    allowed_providers = ["openai", "anthropic", "openrouter", "google", "cohere"]
    filtered_keys = {k: v for k, v in keys.items() if k in allowed_providers}
    
    # Encrypt and store
    encrypted = fernet.encrypt(json.dumps(filtered_keys).encode())
    current_user.model_api_keys_encrypted = encrypted.decode()
    
    await db.commit()
    
    return {"message": "API keys updated", "providers": list(filtered_keys.keys())}
