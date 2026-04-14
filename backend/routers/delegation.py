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
from middleware.rate_limit import limiter, RATE_LIMITS
from auth import get_current_active_user, get_user_from_query_token

import os
import hmac as _hmac
import hashlib

router = APIRouter(prefix="/api/delegate", tags=["delegation"])

# In-memory store for delegation logs (use Redis in production with multiple workers)
delegation_logs: dict[str, list] = {}   # delegation_id -> list of log entries
delegation_status: dict[str, str] = {}  # delegation_id -> status string

# Platform economics
PLATFORM_FEE_PCT = Decimal("0.10")   # 10 % of settled tokens go to Hive

# Delegation safety
MAX_DELEGATION_DEPTH = 5

# HMAC key for signing outbound delegation payloads sent to agents
HIVE_SIGNING_SECRET = os.getenv("HIVE_SIGNING_SECRET", "change-me-in-production")


def _sign_payload(body: bytes, timestamp: str = "") -> str:
    """
    Return HMAC-SHA256 hex digest for a payload.

    The message format matches agent_client.py → _make_signature:
        message = f"{timestamp}.".encode() + body
    When timestamp is empty the prefix is just b"." — callers that don't
    pass a timestamp must use the same convention on both sides.
    """
    message = f"{timestamp}.".encode() + body
    return _hmac.new(
        HIVE_SIGNING_SECRET.encode(),
        message,
        hashlib.sha256,
    ).hexdigest()


async def _settle_delegation(
    db,
    transaction: "Transaction",
    tokens_used: Decimal,
    to_wallet: "Wallet",
    from_wallet: "Wallet",
    task_result: dict | None = None,
) -> None:
    """
    Apply platform fee, transfer settled tokens to the agent, refund remainder
    to the delegating party, and mark the transaction completed.

    Mutates wallet balances and transaction in place; caller must commit.
    """
    # Cap at the escrowed amount
    tokens_used = min(tokens_used, transaction.amount)

    # Platform takes its cut from the agent's share
    platform_fee = (tokens_used * PLATFORM_FEE_PCT).quantize(Decimal("0.0001"))
    agent_receives = tokens_used - platform_fee

    to_wallet.balance += agent_receives
    transaction.platform_fee = platform_fee

    # Refund unused escrow to delegator
    refund = transaction.amount - tokens_used
    if refund > Decimal("0"):
        from_wallet.balance += refund

    transaction.amount = tokens_used
    transaction.task_result = task_result
    transaction.status = TransactionStatus.COMPLETED.value
    transaction.completed_at = datetime.utcnow()


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

    if target_agent.ready is False:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Target agent is busy and not accepting new tasks"
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

    # Deduct balance atomically: flush first, then verify no overdraft.
    # This catches concurrent requests that both passed the pre-check.
    user_wallet.balance -= Decimal(str(delegation.max_tokens))
    await db.flush()
    if user_wallet.balance < 0:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient tokens. Required: {delegation.max_tokens}"
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
        delegating_agent_id=None,   # User delegation — no delegating agent
        executing_agent_id=target_agent.id,
        originating_user_id=current_user.id,
        session_id=delegation.session_id,
        delegation_depth=0,         # Direct human request = depth 0
        task_description=delegation.task_description,
        status=TransactionStatus.PENDING.value,
    )

    # Balance already deducted (flush + overdraft check done above)
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
        
        # If agent responds synchronously with completion, settle immediately
        if agent_response.get('status') == 'completed':
            tokens_used = Decimal(str(agent_response.get('tokens_used', delegation.max_tokens)))
            await _settle_delegation(
                db, transaction, tokens_used,
                to_wallet=target_wallet, from_wallet=user_wallet,
                task_result=agent_response.get('result'),
            )
            await db.commit()
            set_delegation_status(transaction.id, "completed")

            return DelegationResponse(
                delegation_id=transaction.id,
                status="completed",
                message=f"Task completed by {target_agent.name}",
            )

        return DelegationResponse(
            delegation_id=transaction.id,
            status="in_progress",
            message=f"Task accepted by {target_agent.name}. Use /status endpoint to check progress.",
        )

    except AgentTimeoutError:
        transaction.status = TransactionStatus.FAILED.value
        transaction.completed_at = datetime.utcnow()
        transaction.refund_reason = "agent_timeout"
        transaction.task_description += "\n\nFailed: Agent timeout"
        user_wallet.balance += transaction.amount  # Full refund
        await db.commit()
        set_delegation_status(transaction.id, "failed")

        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Target agent did not respond within {delegation.timeout_seconds}s",
        )

    except (AgentConnectionError, AgentClientError) as e:
        transaction.status = TransactionStatus.FAILED.value
        transaction.completed_at = datetime.utcnow()
        transaction.refund_reason = "agent_error"
        transaction.task_description += f"\n\nFailed: {str(e)}"
        user_wallet.balance += transaction.amount  # Full refund
        await db.commit()
        set_delegation_status(transaction.id, "failed")

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to communicate with target agent: {str(e)}",
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

    if target_agent.ready is False:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Target agent is busy and not accepting new tasks"
        )

    # Check if target agent is public or if delegating agent's owner has access
    if not target_agent.is_public and target_agent.owner_id != agent.owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Target agent is not available for delegation"
        )

    # Enforce delegation depth — look up the parent transaction if a session is provided
    current_depth = 0
    if delegation.session_id:
        depth_result = await db.execute(
            select(Transaction.delegation_depth)
            .where(Transaction.session_id == delegation.session_id)
            .order_by(Transaction.delegation_depth.desc())
            .limit(1)
        )
        max_depth_row = depth_result.scalar_one_or_none()
        if max_depth_row is not None:
            current_depth = max_depth_row + 1

    if current_depth >= MAX_DELEGATION_DEPTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Delegation chain too deep (max {MAX_DELEGATION_DEPTH} hops). "
                   "This prevents runaway agent loops."
        )

    # Get wallets
    delegating_wallet = await get_or_create_wallet(agent.owner_id, db)
    target_wallet = await get_or_create_wallet(target_agent.owner_id, db)

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

    # Resolve originating user from the parent transaction in this session
    originating_user_id = None
    if delegation.session_id:
        origin_result = await db.execute(
            select(Transaction.originating_user_id)
            .where(Transaction.session_id == delegation.session_id)
            .limit(1)
        )
        originating_user_id = origin_result.scalar_one_or_none()

    # Create PENDING transaction (escrow tokens)
    transaction = Transaction(
        from_wallet_id=delegating_wallet.id,
        to_wallet_id=target_wallet.id,
        amount=Decimal(str(delegation.max_tokens)),
        transaction_type=TransactionType.DELEGATION.value,
        delegating_agent_id=agent.id,
        executing_agent_id=target_agent.id,
        originating_user_id=originating_user_id,
        session_id=delegation.session_id,
        delegation_depth=current_depth,
        task_description=delegation.task_description,
        status=TransactionStatus.PENDING.value,
    )

    # Atomic escrow: deduct then flush to detect overdraft (prevents TOCTOU race)
    delegating_wallet.balance -= Decimal(str(delegation.max_tokens))
    await db.flush()
    if delegating_wallet.balance < 0:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient tokens. Required: {delegation.max_tokens}"
        )

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
        
        # If agent responds synchronously, settle immediately
        if agent_response.get('status') == 'completed':
            tokens_used = Decimal(str(agent_response.get('tokens_used', delegation.max_tokens)))
            await _settle_delegation(
                db, transaction, tokens_used,
                to_wallet=target_wallet, from_wallet=delegating_wallet,
                task_result=agent_response.get('result'),
            )
            await db.commit()
            set_delegation_status(transaction.id, "completed")

            return DelegationResponse(
                delegation_id=transaction.id,
                status="completed",
                message=f"Task completed by {target_agent.name}",
            )

        return DelegationResponse(
            delegation_id=transaction.id,
            status="in_progress",
            message=f"Task accepted by {target_agent.name}. Use /status endpoint to check progress.",
        )

    except AgentTimeoutError:
        transaction.status = TransactionStatus.FAILED.value
        transaction.completed_at = datetime.utcnow()
        transaction.refund_reason = "agent_timeout"
        transaction.task_description += "\n\nFailed: Agent timeout"
        delegating_wallet.balance += transaction.amount
        await db.commit()
        set_delegation_status(transaction.id, "failed")

        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Target agent did not respond within {delegation.timeout_seconds}s",
        )

    except (AgentConnectionError, AgentClientError) as e:
        transaction.status = TransactionStatus.FAILED.value
        transaction.completed_at = datetime.utcnow()
        transaction.refund_reason = "agent_error"
        transaction.task_description += f"\n\nFailed: {str(e)}"
        delegating_wallet.balance += transaction.amount
        await db.commit()
        set_delegation_status(transaction.id, "failed")

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to communicate with target agent: {str(e)}",
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
    
    # Prefer in-memory status (real-time) over stale DB value
    live_status = delegation_status.get(transaction.id, transaction.status)

    return {
        "delegation_id": transaction.id,
        "status": live_status,
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
    current_user: User = Depends(get_user_from_query_token),
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
    
    tokens_used = Decimal(str(completion.tokens_used))

    target_wallet_result = await db.execute(
        select(Wallet).where(Wallet.id == transaction.to_wallet_id)
    )
    target_wallet = target_wallet_result.scalar_one()

    from_wallet_result = await db.execute(
        select(Wallet).where(Wallet.id == transaction.from_wallet_id)
    )
    from_wallet = from_wallet_result.scalar_one()

    await _settle_delegation(
        db, transaction, tokens_used,
        to_wallet=target_wallet, from_wallet=from_wallet,
        task_result=completion.result,
    )
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
    transaction.refund_reason = "agent_error"
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


def _verify_callback_signature(request: Request, body: bytes) -> None:
    """
    Verify the HMAC-SHA256 signature on inbound callback requests.

    Expected headers (same scheme as agent_client.py → send_delegation_task):
        X-Hive-Timestamp: <unix epoch seconds>
        X-Hive-Signature: sha256=HMAC(f"{timestamp}.".encode() + body)

    Raises HTTP 401 if the signature is absent or invalid.
    """
    sig_header = request.headers.get("X-Hive-Signature", "")
    ts_header  = request.headers.get("X-Hive-Timestamp", "")

    if not sig_header or not ts_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Hive-Signature or X-Hive-Timestamp header",
        )

    expected = f"sha256={_sign_payload(body, ts_header)}"
    # constant-time compare prevents timing attacks
    if not _hmac.compare_digest(sig_header, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid callback signature",
        )


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

    Callers MUST include valid X-Hive-Signature and X-Hive-Timestamp headers
    (HMAC-SHA256 over the raw request body, signed with HIVE_SIGNING_SECRET).
    This is the same signing scheme used by Hive when it sends tasks to agents,
    so agents that simply echo the signature back are automatically compliant.
    """
    raw_body = await request.body()
    _verify_callback_signature(request, raw_body)
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
    
    tokens_used = Decimal(str(callback_data.tokens_used))

    target_wallet_result = await db.execute(
        select(Wallet).where(Wallet.id == transaction.to_wallet_id)
    )
    target_wallet = target_wallet_result.scalar_one()

    from_wallet_result = await db.execute(
        select(Wallet).where(Wallet.id == transaction.from_wallet_id)
    )
    from_wallet = from_wallet_result.scalar_one()

    await _settle_delegation(
        db, transaction, tokens_used,
        to_wallet=target_wallet, from_wallet=from_wallet,
        task_result=callback_data.result,
    )
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
        Agent.status.in_([AgentStatus.ACTIVE.value, AgentStatus.IDLE.value]),
        Agent.ready != False  # exclude agents that have explicitly marked themselves not ready
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

        # Skip agents not ready to accept work
        if a.ready is False:
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
    agent: Agent = Depends(get_agent_from_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Receive progress updates from the executing agent (authenticated via API key).

    Agents call this to stream thinking / intermediate steps back to the delegator.
    These are broadcast over the SSE stream to any listening clients.

    Expected body:
    {
        "level": "info" | "thinking" | "action" | "success" | "error",
        "message": "What the agent is doing",
        "data": {optional additional data}
    }
    """
    result = await db.execute(
        select(Transaction).where(Transaction.id == delegation_id)
    )
    transaction = result.scalar_one_or_none()

    if not transaction:
        raise HTTPException(status_code=404, detail="Delegation not found")

    # Only the executing agent may post progress
    if transaction.executing_agent_id != agent.id:
        raise HTTPException(status_code=403, detail="Not authorized to update this delegation")

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
