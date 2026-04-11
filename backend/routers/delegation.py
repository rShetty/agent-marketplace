"""Agent-to-agent delegation routes."""
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status, Header, BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.agent import Agent, AgentStatus
from models.wallet import Wallet
from models.transaction import Transaction, TransactionType, TransactionStatus
from schemas import DelegationRequest, DelegationResponse, DelegationComplete
from routers.agent_api import get_agent_from_api_key
from routers.wallet import get_or_create_wallet
from services.agent_client import get_agent_client, AgentTimeoutError, AgentConnectionError, AgentClientError
from middleware.rate_limit import limiter, RATE_LIMITS

router = APIRouter(prefix="/api/delegate", tags=["delegation"])


@router.post("/request", response_model=DelegationResponse)
@limiter.limit(RATE_LIMITS["delegate_request"])
async def request_delegation(
    request: Request,
    delegation: DelegationRequest,
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Request another agent to perform work (agent-to-agent delegation).
    
    Requires: Agent API key in X-API-Key header
    Creates: PENDING transaction with escrowed tokens
    Returns: delegation_id to track status
    """
    # Get target agent
    result = await db.execute(
        select(Agent).where(Agent.id == delegation.target_agent_id)
    )
    target_agent = result.scalar_one_or_none()
    
    if not target_agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target agent not found"
        )
    
    if target_agent.status not in [AgentStatus.ACTIVE.value, AgentStatus.IDLE.value]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Target agent is {target_agent.status}"
        )
    
    # Check if target agent is public or if delegating agent's owner has access
    if not target_agent.is_public and target_agent.owner_id != agent.owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Target agent is not available for delegation"
        )
    
    # Get wallets
    delegating_wallet = await get_or_create_wallet(agent.owner_id, db)
    target_wallet = await get_or_create_wallet(target_agent.owner_id, db)
    
    # Check balance
    if delegating_wallet.balance < Decimal(str(delegation.max_tokens)):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient tokens. Required: {delegation.max_tokens}, Available: {delegating_wallet.balance}"
        )
    
    # Check pricing model
    if target_agent.pricing_model:
        pricing_type = target_agent.pricing_model.get("type")
        if pricing_type == "token":
            required_rate = Decimal(str(target_agent.pricing_model.get("rate", 0)))
            if delegation.max_tokens < float(required_rate):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Agent requires minimum {required_rate} tokens"
                )
    
    # Create PENDING transaction (escrow tokens)
    transaction = Transaction(
        from_wallet_id=delegating_wallet.id,
        to_wallet_id=target_wallet.id,
        amount=Decimal(str(delegation.max_tokens)),
        transaction_type=TransactionType.DELEGATION.value,
        delegating_agent_id=agent.id,
        executing_agent_id=target_agent.id,
        task_description=delegation.task_description,
        status=TransactionStatus.PENDING.value
    )
    
    # Escrow tokens (deduct from delegating wallet)
    delegating_wallet.balance -= Decimal(str(delegation.max_tokens))
    
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    
    # Call target agent's endpoint with task (async, non-blocking)
    try:
        agent_client = get_agent_client(timeout=delegation.timeout_seconds)
        
        # Make the HTTP call to the target agent
        agent_response = await agent_client.send_delegation_task(
            target_endpoint=target_agent.endpoint_url,
            delegation_id=transaction.id,
            task_description=delegation.task_description,
            max_tokens=delegation.max_tokens,
            callback_url=delegation.callback_url,
            context=delegation.context,
            timeout=delegation.timeout_seconds
        )
        
        print(f"🔄 Delegation sent: {agent.name} → {target_agent.name} ({delegation.max_tokens} tokens)")
        print(f"   Agent response: {agent_response.get('status', 'unknown')}")
        
        # If agent responds synchronously with completion, handle it immediately
        if agent_response.get('status') == 'completed':
            # Agent completed immediately - update transaction
            transaction.status = TransactionStatus.COMPLETED.value
            transaction.completed_at = datetime.utcnow()
            
            tokens_used = Decimal(str(agent_response.get('tokens_used', delegation.max_tokens)))
            if tokens_used > transaction.amount:
                tokens_used = transaction.amount
            
            # Transfer tokens
            target_wallet.balance += tokens_used
            
            # Refund unused
            if tokens_used < transaction.amount:
                delegating_wallet.balance += (transaction.amount - tokens_used)
            
            transaction.amount = tokens_used
            await db.commit()
            
            return DelegationResponse(
                delegation_id=transaction.id,
                status="completed",
                message=f"Task completed by {target_agent.name}"
            )
        
        return DelegationResponse(
            delegation_id=transaction.id,
            status="in_progress",
            message=f"Task accepted by {target_agent.name}. Use /status endpoint to check progress."
        )
        
    except AgentTimeoutError:
        # Agent didn't respond in time - mark as failed and refund
        transaction.status = TransactionStatus.FAILED.value
        transaction.completed_at = datetime.utcnow()
        transaction.task_description += "\n\nFailed: Agent timeout"
        delegating_wallet.balance += transaction.amount  # Refund
        await db.commit()
        
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Target agent did not respond within {delegation.timeout_seconds}s"
        )
        
    except (AgentConnectionError, AgentClientError) as e:
        # Failed to reach agent - mark as failed and refund
        transaction.status = TransactionStatus.FAILED.value
        transaction.completed_at = datetime.utcnow()
        transaction.task_description += f"\n\nFailed: {str(e)}"
        delegating_wallet.balance += transaction.amount  # Refund
        await db.commit()
        
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to communicate with target agent: {str(e)}"
        )


@router.get("/{delegation_id}/status")
async def get_delegation_status(
    delegation_id: str,
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Check status of a delegation request."""
    result = await db.execute(
        select(Transaction).where(Transaction.id == delegation_id)
    )
    transaction = result.scalar_one_or_none()
    
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delegation not found"
        )
    
    # Verify agent is involved in this delegation
    if transaction.delegating_agent_id != agent.id and transaction.executing_agent_id != agent.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this delegation"
        )
    
    return {
        "delegation_id": transaction.id,
        "status": transaction.status,
        "amount": float(transaction.amount),
        "task_description": transaction.task_description,
        "created_at": transaction.created_at,
        "completed_at": transaction.completed_at
    }


@router.post("/{delegation_id}/complete")
@limiter.limit(RATE_LIMITS["delegate_complete"])
async def complete_delegation(
    request: Request,
    delegation_id: str,
    completion: DelegationComplete,
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Mark delegation as complete (called by executing agent).
    Releases escrowed tokens to executing agent's owner.
    """
    result = await db.execute(
        select(Transaction).where(Transaction.id == delegation_id)
    )
    transaction = result.scalar_one_or_none()
    
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delegation not found"
        )
    
    # Verify caller is the executing agent
    if transaction.executing_agent_id != agent.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only executing agent can complete delegation"
        )
    
    if transaction.status != TransactionStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Delegation is already {transaction.status}"
        )
    
    # Calculate actual tokens used
    tokens_used = Decimal(str(completion.tokens_used))
    if tokens_used > transaction.amount:
        tokens_used = transaction.amount  # Cap at max
    
    # Transfer tokens to target wallet
    target_wallet_result = await db.execute(
        select(Wallet).where(Wallet.id == transaction.to_wallet_id)
    )
    target_wallet = target_wallet_result.scalar_one()
    target_wallet.balance += tokens_used
    
    # Refund unused tokens to delegating wallet
    if tokens_used < transaction.amount:
        refund = transaction.amount - tokens_used
        delegating_wallet_result = await db.execute(
            select(Wallet).where(Wallet.id == transaction.from_wallet_id)
        )
        delegating_wallet = delegating_wallet_result.scalar_one()
        delegating_wallet.balance += refund
    
    # Update transaction
    transaction.status = TransactionStatus.COMPLETED.value
    transaction.completed_at = datetime.utcnow()
    transaction.amount = tokens_used  # Update to actual amount
    
    await db.commit()
    
    # TODO: Send callback to delegating agent if callback_url was provided
    
    print(f"✅ Delegation completed: {delegation_id} ({tokens_used} tokens)")
    
    return {
        "success": True,
        "delegation_id": delegation_id,
        "tokens_used": float(tokens_used),
        "status": "completed"
    }


@router.post("/{delegation_id}/fail")
async def fail_delegation(
    delegation_id: str,
    reason: str,
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Mark delegation as failed (called by executing agent).
    Refunds all escrowed tokens to delegating agent's owner.
    """
    result = await db.execute(
        select(Transaction).where(Transaction.id == delegation_id)
    )
    transaction = result.scalar_one_or_none()
    
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delegation not found"
        )
    
    # Verify caller is the executing agent
    if transaction.executing_agent_id != agent.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only executing agent can fail delegation"
        )
    
    if transaction.status != TransactionStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Delegation is already {transaction.status}"
        )
    
    # Refund all tokens to delegating wallet
    delegating_wallet_result = await db.execute(
        select(Wallet).where(Wallet.id == transaction.from_wallet_id)
    )
    delegating_wallet = delegating_wallet_result.scalar_one()
    delegating_wallet.balance += transaction.amount
    
    # Update transaction
    transaction.status = TransactionStatus.FAILED.value
    transaction.completed_at = datetime.utcnow()
    transaction.task_description += f"\n\nFailed: {reason}"
    
    await db.commit()
    
    print(f"❌ Delegation failed: {delegation_id} - {reason}")
    
    return {
        "success": True,
        "delegation_id": delegation_id,
        "status": "failed",
        "refunded": float(transaction.amount)
    }


@router.post("/{delegation_id}/callback")
@limiter.limit(RATE_LIMITS["delegate_callback"])
async def delegation_callback(
    request: Request,
    delegation_id: str,
    callback_data: DelegationComplete,
    db: AsyncSession = Depends(get_db)
):
    """
    Callback endpoint for agents to report delegation completion asynchronously.
    This is called by the executing agent when work is done.
    
    Note: This endpoint is public (no auth) but validates delegation_id.
    In production, consider adding HMAC signature verification.
    """
    result = await db.execute(
        select(Transaction).where(Transaction.id == delegation_id)
    )
    transaction = result.scalar_one_or_none()
    
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delegation not found"
        )
    
    if transaction.status != TransactionStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Delegation is already {transaction.status}"
        )
    
    # Calculate actual tokens used
    tokens_used = Decimal(str(callback_data.tokens_used))
    if tokens_used > transaction.amount:
        tokens_used = transaction.amount
    
    # Transfer tokens to target wallet
    target_wallet_result = await db.execute(
        select(Wallet).where(Wallet.id == transaction.to_wallet_id)
    )
    target_wallet = target_wallet_result.scalar_one()
    target_wallet.balance += tokens_used
    
    # Refund unused tokens
    if tokens_used < transaction.amount:
        refund = transaction.amount - tokens_used
        delegating_wallet_result = await db.execute(
            select(Wallet).where(Wallet.id == transaction.from_wallet_id)
        )
        delegating_wallet = delegating_wallet_result.scalar_one()
        delegating_wallet.balance += refund
    
    # Update transaction
    transaction.status = TransactionStatus.COMPLETED.value
    transaction.completed_at = datetime.utcnow()
    transaction.amount = tokens_used
    
    await db.commit()
    
    print(f"✅ Delegation callback received: {delegation_id} ({tokens_used} tokens)")
    
    return {
        "success": True,
        "delegation_id": delegation_id,
        "tokens_used": float(tokens_used),
        "status": "completed",
        "message": "Delegation marked as completed"
    }


@router.get("/discover")
async def discover_agents_for_delegation(
    skill: str = None,
    max_cost: float = None,
    min_rating: float = None,
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Discover agents available for delegation.
    Returns public agents matching criteria.
    """
    query = select(Agent).where(
        Agent.is_public == True,
        Agent.status.in_([AgentStatus.ACTIVE.value, AgentStatus.IDLE.value])
    )
    
    # Filter by skill if specified
    if skill:
        from models.agent_skill import AgentSkill
        query = query.join(AgentSkill).join(AgentSkill.skill).where(
            AgentSkill.skill.has(name=skill)
        )
    
    result = await db.execute(query.limit(20))
    agents = result.scalars().all()
    
    # Filter by pricing and rating
    discovered = []
    for a in agents:
        # Skip self
        if a.id == agent.id:
            continue
        
        # Check pricing
        if max_cost and a.pricing_model:
            if a.pricing_model.get("type") == "token":
                rate = a.pricing_model.get("rate", 0)
                if rate > max_cost:
                    continue
        
        discovered.append({
            "id": a.id,
            "name": a.name,
            "slug": a.slug,
            "description": a.marketplace_description or a.description,
            "pricing_model": a.pricing_model,
            "tags": a.tags or [],
            "status": a.status,
            "last_seen": a.last_seen
        })
    
    return {
        "agents": discovered,
        "count": len(discovered)
    }
