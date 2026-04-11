"""Agent review and reputation routes."""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from database import get_db
from models.user import User
from models.agent_review import AgentReview
from models.transaction import Transaction, TransactionStatus
from schemas import AgentReviewCreate, AgentReviewResponse
from auth import get_current_active_user

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


@router.post("", response_model=AgentReviewResponse)
async def submit_review(
    review_data: AgentReviewCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Submit a review for an agent after a completed delegation.
    Only users who paid for completed work can review.
    One review per delegation.
    """
    # Verify delegation exists and is completed
    delegation_result = await db.execute(
        select(Transaction).where(Transaction.id == review_data.delegation_id)
    )
    delegation = delegation_result.scalar_one_or_none()
    
    if not delegation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delegation not found"
        )
    
    if delegation.status != TransactionStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only review completed delegations"
        )
    
    # Verify user owns the delegating agent (i.e. they paid)
    from models.agent import Agent
    delegating_agent_result = await db.execute(
        select(Agent).where(Agent.id == delegation.delegating_agent_id)
    )
    delegating_agent = delegating_agent_result.scalar_one_or_none()
    
    if not delegating_agent or delegating_agent.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the user who paid for work can review"
        )
    
    # Check if review already exists for this delegation
    existing_review_result = await db.execute(
        select(AgentReview).where(AgentReview.delegation_id == review_data.delegation_id)
    )
    existing_review = existing_review_result.scalar_one_or_none()
    
    if existing_review:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review already submitted for this delegation"
        )
    
    # Create review
    review = AgentReview(
        agent_id=review_data.agent_id,
        reviewer_user_id=current_user.id,
        delegation_id=review_data.delegation_id,
        rating=review_data.rating,
        comment=review_data.comment
    )
    
    db.add(review)
    await db.commit()
    await db.refresh(review)
    
    print(f"⭐ Review submitted: Agent {review_data.agent_id} rated {review_data.rating}/5")
    
    return review


@router.get("/agent/{agent_id}")
async def get_agent_reviews(
    agent_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """Get reviews for a specific agent."""
    result = await db.execute(
        select(AgentReview)
        .where(AgentReview.agent_id == agent_id)
        .order_by(AgentReview.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    reviews = result.scalars().all()
    
    # Calculate stats
    stats_result = await db.execute(
        select(
            func.avg(AgentReview.rating),
            func.count(AgentReview.id),
            func.count(func.distinct(AgentReview.reviewer_user_id))
        )
        .where(AgentReview.agent_id == agent_id)
    )
    avg_rating, total_reviews, unique_reviewers = stats_result.one()
    
    return {
        "items": [AgentReviewResponse.model_validate(r) for r in reviews],
        "stats": {
            "average_rating": float(avg_rating) if avg_rating else None,
            "total_reviews": total_reviews or 0,
            "unique_reviewers": unique_reviewers or 0
        },
        "limit": limit,
        "offset": offset
    }


@router.get("/user/given")
async def get_user_given_reviews(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get reviews given by current user."""
    result = await db.execute(
        select(AgentReview)
        .where(AgentReview.reviewer_user_id == current_user.id)
        .order_by(AgentReview.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    reviews = result.scalars().all()
    
    return {
        "items": [AgentReviewResponse.model_validate(r) for r in reviews],
        "limit": limit,
        "offset": offset
    }
