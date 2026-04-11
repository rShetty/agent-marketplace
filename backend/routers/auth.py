"""Authentication routes."""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.user import User
from schemas import UserCreate, UserResponse, Token, LoginRequest
from auth import verify_password, get_password_hash, create_access_token, get_current_active_user
from middleware.rate_limit import limiter, RATE_LIMITS

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer()


@router.post("/register", response_model=UserResponse)
@limiter.limit(RATE_LIMITS["auth_register"])
async def register(request: Request, user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    """Register a new user."""
    # Check if email already exists
    result = await db.execute(select(User).where(User.email == user_data.email))
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create new user
    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        email=user_data.email,
        hashed_password=hashed_password,
        name=user_data.name
    )
    
    db.add(new_user)
    await db.flush()  # Flush to get user.id
    
    # Create wallet with initial balance
    from models.wallet import Wallet
    from decimal import Decimal
    
    wallet = Wallet(
        user_id=new_user.id,
        balance=Decimal("100.00")  # Initial 100 tokens
    )
    db.add(wallet)
    
    await db.commit()
    await db.refresh(new_user)
    
    print(f"🎉 New user registered: {new_user.email} (Wallet created with 100 tokens)")
    
    return new_user


@router.post("/login", response_model=Token)
@limiter.limit(RATE_LIMITS["auth_login"])
async def login(request: Request, login_data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login and get access token."""
    # Find user by email
    result = await db.execute(select(User).where(User.email == login_data.email))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Verify password
    if not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create access token
    access_token = create_access_token(data={"sub": user.id})
    
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_active_user),
):
    """Get the currently authenticated user's profile."""
    return current_user
