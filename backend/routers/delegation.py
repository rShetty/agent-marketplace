"""Agent-to-agent delegation routes."""
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status, Header, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import json
import asyncio

from database import get_db, async_session_maker
from models.agent import Agent, AgentStatus
from models.user import User
from models.wallet import Wallet
from models.transaction import Transaction, TransactionType, TransactionStatus
from schemas import DelegationRequest, DelegationResponse, DelegationComplete
from routers.agent_api import get_agent_from_api_key
from routers.wallet import get_or_create_wallet
from services.agent_client import get_agent_client, AgentTimeoutError, AgentConnectionError, AgentClientError
from auth import get_current_active_user
from middleware.rate_limit import limiter, RATE_LIMITS
from auth import get_current_active_user

router = APIRouter(prefix="/api/delegate", tags=["delegation"])

# In-memory store for delegation logs (in production, use Redis or similar)
delegation_logs = {}  # delegation_id -> list of log entries
delegation_status = {}  # delegation_id -> status


@router.post("/user-request", response_model=DelegationResponse)
@limiter.limit(RATE_LIMITS["delegate_request"])
async def user_request_delegation(
    request: Request,
    delegation: DelegationRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Request another agent to perform work (user-to-agent delegation).
    
    Requires: JWT authentication (user token)
    Creates: PENDING transaction with escrowed tokens from user's wallet
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
    
    # Check if target agent is public
    if not target_agent.is_public:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Target agent is not available for public delegation"
        )
    
    # Get user wallet
    user_wallet = await get_or_create_wallet(current_user.id, db)
    
    # Get target agent owner's wallet
    target_wallet = await get_or_create_wallet(target_agent.owner_id, db)
    
    # Check balance
    if user_wallet.balance < Decimal(str(delegation.max_tokens)):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient tokens. Required: {delegation.max_tokens}, Available: {user_wallet.balance}"
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
        from_wallet_id=user_wallet.id,
        to_wallet_id=target_wallet.id,
        amount=Decimal(str(delegation.max_tokens)),
        transaction_type=TransactionType.DELEGATION.value,
        delegating_agent_id=None,  # User delegation, no delegating agent
        executing_agent_id=target_agent.id,
        task_description=delegation.task_description,
        status=TransactionStatus.PENDING.value
    )
    
    # Escrow tokens (deduct from user wallet)
    user_wallet.balance -= Decimal(str(delegation.max_tokens))
    
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    
    # Initialize delegation tracking
    delegation_logs[transaction.id] = []
    delegation_status[transaction.id] = "pending"
    
    # Add initial log
    add_delegation_log(transaction.id, "info", f"Starting delegation to {target_agent.name}")
    
    # Call target agent's endpoint with task (async, non-blocking)
    try:
        agent_client = get_agent_client(timeout=delegation.timeout_seconds)
        
        add_delegation_log(transaction.id, "info", f"Contacting agent at {target_agent.endpoint_url}")
        
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
        
        print(f"🔄 User delegation sent: User {current_user.email} → {target_agent.name} ({delegation.max_tokens} tokens)")
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
                user_wallet.balance += (transaction.amount - tokens_used)
            
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
        user_wallet.balance += transaction.amount  # Refund
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
        user_wallet.balance += transaction.amount  # Refund
        await db.commit()
        
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to communicate with target agent: {str(e)}"
        )


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
    
    # Initialize delegation tracking
    delegation_logs[transaction.id] = []
    delegation_status[transaction.id] = "pending"
    
    # Add initial log
    add_delegation_log(transaction.id, "info", f"Starting delegation to {target_agent.name}")
    
    # Call target agent's endpoint with task (async, non-blocking)
    try:
        agent_client = get_agent_client(timeout=delegation.timeout_seconds)
        
        add_delegation_log(transaction.id, "info", f"Contacting agent at {target_agent.endpoint_url}")
        
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


@router.get("/{delegation_id}/user-status")
async def get_user_delegation_status(
    delegation_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Check status of a user's delegation request."""
    result = await db.execute(
        select(Transaction).where(Transaction.id == delegation_id)
    )
    transaction = result.scalar_one_or_none()
    
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delegation not found"
        )
    
    # Verify user is involved in this delegation (as delegator)
    user_wallet = await get_or_create_wallet(current_user.id, db)
    
    if transaction.from_wallet_id != user_wallet.id:
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


@router.get("/{delegation_id}/user-logs")
async def get_user_delegation_logs(
    delegation_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all logs for a user's delegation (non-streaming).
    """
    # Verify delegation exists and user has access
    result = await db.execute(
        select(Transaction).where(Transaction.id == delegation_id)
    )
    transaction = result.scalar_one_or_none()
    
    if not transaction:
        raise HTTPException(status_code=404, detail="Delegation not found")
    
    # Verify user owns this delegation
    user_wallet = await get_or_create_wallet(current_user.id, db)
    
    if transaction.from_wallet_id != user_wallet.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this delegation")
    
    logs = delegation_logs.get(delegation_id, [])
    return {
        "delegation_id": delegation_id,
        "logs": logs,
        "status": transaction.status,
        "total_logs": len(logs)
    }


@router.get("/{delegation_id}/user-stream")
async def stream_user_delegation(
    delegation_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Stream delegation progress using Server-Sent Events (user version).
    """
    # Verify delegation exists and user has access
    result = await db.execute(
        select(Transaction).where(Transaction.id == delegation_id)
    )
    transaction = result.scalar_one_or_none()
    
    if not transaction:
        raise HTTPException(status_code=404, detail="Delegation not found")
    
    # Verify user owns this delegation
    user_wallet = await get_or_create_wallet(current_user.id, db)
    
    if transaction.from_wallet_id != user_wallet.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this delegation")

    async def event_generator():
        """Generate SSE events for delegation progress (user-facing)."""
        last_log_index = 0
        last_sent_status = delegation_status.get(delegation_id, "pending")

        while True:
            current_status = delegation_status.get(delegation_id, "unknown")
            current_logs = delegation_logs.get(delegation_id, [])

            # Send any new log entries
            for i in range(last_log_index, len(current_logs)):
                yield f"data: {json.dumps({'type': 'log', 'data': current_logs[i]})}\n\n"
                last_log_index = i + 1

            # Notify on status change
            if current_status != last_sent_status:
                yield f"data: {json.dumps({'type': 'status', 'data': {'status': current_status}})}\n\n"
                last_sent_status = current_status

            # Stop streaming once terminal
            if current_status in ("completed", "failed"):
                break

            # Heartbeat to keep connection alive
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/user-delegations")
async def get_user_delegations(
    status: str = None,
    limit: int = 20,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get delegations for current user (as delegator).
    """
    user_wallet = await get_or_create_wallet(current_user.id, db)
    
    query = select(Transaction).where(
        Transaction.from_wallet_id == user_wallet.id
    )
    
    if status:
        query = query.where(Transaction.status == status)
    
    query = query.order_by(Transaction.created_at.desc()).limit(limit)
    result = await db.execute(query)
    transactions = result.scalars().all()
    
    return {
        "delegations": [
            {
                "id": t.id,
                "task_description": t.task_description,
                "amount": float(t.amount),
                "status": t.status,
                "created_at": t.created_at.isoformat(),
                "completed_at": t.completed_at.isoformat() if t.completed_at else None
            }
            for t in transactions
        ],
        "total": len(transactions)
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

    # Update in-memory status so SSE streams detect completion
    set_delegation_status(delegation_id, "completed")

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

    # Update in-memory status so SSE streams detect failure
    set_delegation_status(delegation_id, "failed")

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

    # Update in-memory status so SSE streams detect completion
    set_delegation_status(delegation_id, "completed")

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


# ============== Streaming and Logging ==============

def add_delegation_log(delegation_id: str, level: str, message: str, data: dict = None, source: str = "system"):
    """Add a log entry for a delegation."""
    if delegation_id not in delegation_logs:
        delegation_logs[delegation_id] = []
    
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "level": level,
        "message": message,
        "data": data or {},
        "source": source  # "system" or "agent"
    }
    
    delegation_logs[delegation_id].append(log_entry)
    print(f"📝 [{delegation_id[:8]}] [{source.upper()}] {level.upper()}: {message}")


def set_delegation_status(delegation_id: str, status: str):
    """Update delegation status."""
    delegation_status[delegation_id] = status
    add_delegation_log(delegation_id, "info", f"Status changed to: {status}", source="system")


@router.post("/{delegation_id}/progress")
async def agent_progress_update(
    delegation_id: str,
    progress: dict,
    request: Request
):
    """
    Receive progress updates from executing agent.
    Agents can call this to stream their progress/thinking back to the delegator.
    
    Expected progress format:
    {
        "level": "info" | "thinking" | "action" | "success" | "error",
        "message": "What the agent is doing",
        "data": {optional additional data}
    }
    """
    # Verify delegation exists
    async with async_session_maker() as session:
        result = await session.execute(
            select(Transaction).where(Transaction.id == delegation_id)
        )
        transaction = result.scalar_one_or_none()
        
        if not transaction:
            raise HTTPException(status_code=404, detail="Delegation not found")
        
        # Log the progress update
        level = progress.get("level", "info")
        message = progress.get("message", "No message")
        data = progress.get("data", {})
        
        add_delegation_log(delegation_id, level, message, data, source="agent")
        
        return {
            "success": True,
            "delegation_id": delegation_id,
            "message": "Progress update received"
        }


@router.get("/{delegation_id}/stream")
async def stream_delegation(
    delegation_id: str,
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Stream delegation progress using Server-Sent Events.
    """
    # Verify delegation exists and agent has access
    result = await db.execute(
        select(Transaction).where(Transaction.id == delegation_id)
    )
    transaction = result.scalar_one_or_none()
    
    if not transaction:
        raise HTTPException(status_code=404, detail="Delegation not found")
    
    if transaction.delegating_agent_id != agent.id and transaction.executing_agent_id != agent.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this delegation")

    async def event_generator():
        """Generate SSE events for delegation progress (agent-facing)."""
        last_log_index = 0
        last_sent_status = delegation_status.get(delegation_id, "pending")

        while True:
            current_status = delegation_status.get(delegation_id, "unknown")
            current_logs = delegation_logs.get(delegation_id, [])

            # Send any new log entries
            for i in range(last_log_index, len(current_logs)):
                yield f"data: {json.dumps({'type': 'log', 'data': current_logs[i]})}\n\n"
                last_log_index = i + 1

            # Notify on status change
            if current_status != last_sent_status:
                yield f"data: {json.dumps({'type': 'status', 'data': {'status': current_status}})}\n\n"
                last_sent_status = current_status

            # Stop streaming once terminal
            if current_status in ("completed", "failed"):
                break

            # Heartbeat to keep connection alive
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/{delegation_id}/logs")
async def get_delegation_logs(
    delegation_id: str,
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all logs for a delegation (non-streaming).
    """
    # Verify delegation exists and agent has access
    result = await db.execute(
        select(Transaction).where(Transaction.id == delegation_id)
    )
    transaction = result.scalar_one_or_none()
    
    if not transaction:
        raise HTTPException(status_code=404, detail="Delegation not found")
    
    if transaction.delegating_agent_id != agent.id and transaction.executing_agent_id != agent.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this delegation")
    
    logs = delegation_logs.get(delegation_id, [])
    return {
        "delegation_id": delegation_id,
        "logs": logs,
        "status": transaction.status,
        "total_logs": len(logs)
    }


@router.get("/my-delegations")
async def get_my_delegations(
    status: str = None,
    limit: int = 20,
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Get delegations involving this agent (as delegator or executor).
    """
    query = select(Transaction).where(
        (Transaction.delegating_agent_id == agent.id) | 
        (Transaction.executing_agent_id == agent.id)
    )
    
    if status:
        query = query.where(Transaction.status == status)
    
    query = query.order_by(Transaction.created_at.desc()).limit(limit)
    result = await db.execute(query)
    transactions = result.scalars().all()
    
    return {
        "delegations": [
            {
                "id": t.id,
                "task_description": t.task_description,
                "amount": float(t.amount),
                "status": t.status,
                "created_at": t.created_at.isoformat(),
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "role": "delegator" if t.delegating_agent_id == agent.id else "executor"
            }
            for t in transactions
        ],
        "total": len(transactions)
    }
