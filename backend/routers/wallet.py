"""Wallet and token management routes."""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from decimal import Decimal

from database import get_db
from models.user import User
from models.wallet import Wallet
from models.transaction import Transaction
from schemas import WalletResponse, TransactionResponse
from auth import get_current_active_user

router = APIRouter(prefix="/api/wallet", tags=["wallet"])


async def get_or_create_wallet(user_id: str, db: AsyncSession) -> Wallet:
    """Get user's wallet or create one if it doesn't exist."""
    result = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id)
    )
    wallet = result.scalar_one_or_none()
    
    if not wallet:
        wallet = Wallet(
            user_id=user_id,
            balance=Decimal("100.00")  # Initial balance
        )
        db.add(wallet)
        await db.commit()
        await db.refresh(wallet)
    
    return wallet


@router.get("/balance", response_model=WalletResponse)
async def get_wallet_balance(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get current user's wallet balance."""
    wallet = await get_or_create_wallet(current_user.id, db)
    return wallet


@router.get("/transactions")
async def get_transaction_history(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get transaction history for current user."""
    wallet = await get_or_create_wallet(current_user.id, db)
    
    # Get transactions where user is sender or receiver
    result = await db.execute(
        select(Transaction)
        .where(
            (Transaction.from_wallet_id == wallet.id) |
            (Transaction.to_wallet_id == wallet.id)
        )
        .order_by(desc(Transaction.created_at))
        .limit(limit)
        .offset(offset)
    )
    transactions = result.scalars().all()
    
    return {
        "items": [TransactionResponse.model_validate(t) for t in transactions],
        "limit": limit,
        "offset": offset
    }


@router.post("/admin/grant")
async def admin_grant_tokens(
    user_id: str,
    amount: float,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Admin endpoint to grant tokens to a user.
    Requires admin privileges.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )
    
    # Get target user's wallet
    wallet = await get_or_create_wallet(user_id, db)
    
    # Create admin grant transaction
    from models.transaction import TransactionType, TransactionStatus
    transaction = Transaction(
        from_wallet_id=wallet.id,  # Self-referencing for grants
        to_wallet_id=wallet.id,
        amount=Decimal(str(amount)),
        transaction_type=TransactionType.ADMIN_GRANT.value,
        status=TransactionStatus.COMPLETED.value,
        task_description=f"Admin grant by {current_user.email}"
    )
    
    # Update balance
    wallet.balance += Decimal(str(amount))
    
    db.add(transaction)
    await db.commit()
    await db.refresh(wallet)
    
    return {
        "success": True,
        "wallet_id": wallet.id,
        "new_balance": float(wallet.balance),
        "amount_granted": amount
    }
