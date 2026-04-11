"""Pydantic schemas for API requests/responses."""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, EmailStr, ConfigDict, field_validator
from datetime import datetime


class HiveBaseModel(BaseModel):
    """Base model with protected namespace config to avoid model_* warnings."""
    model_config = ConfigDict(protected_namespaces=())


# ============== User Schemas ==============

class UserBase(HiveBaseModel):
    email: EmailStr
    name: str


class UserCreate(UserBase):
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class UserResponse(UserBase):
    id: str
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class UserUpdate(HiveBaseModel):
    name: Optional[str] = None
    model_api_keys: Optional[Dict[str, str]] = None


# ============== Auth Schemas ==============

class Token(HiveBaseModel):
    access_token: str
    token_type: str


class TokenData(HiveBaseModel):
    sub: Optional[str] = None


class LoginRequest(HiveBaseModel):
    email: EmailStr
    password: str


# ============== Skill Schemas ==============

class SkillBase(HiveBaseModel):
    name: str
    display_name: str
    description: str
    tier: str = "core"
    category: str = "general"
    required_env_vars: List[str] = []


class SkillCreate(SkillBase):
    pass


class SkillResponse(SkillBase):
    id: str
    is_active: bool
    
    model_config = ConfigDict(from_attributes=True)

    @field_validator("is_active", mode="before")
    @classmethod
    def coerce_is_active(cls, v):
        if isinstance(v, str):
            return v.lower() == "true"
        return bool(v)


# ============== AgentSkill Schemas (defined before AgentDetailResponse) ==============

class AgentSkillCreate(HiveBaseModel):
    skill_id: str
    config: Optional[Dict[str, str]] = None


class AgentSkillResponse(HiveBaseModel):
    id: str
    skill_id: str
    config: Optional[Dict[str, Any]] = None
    added_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ============== Agent Schemas ==============

class AgentBase(HiveBaseModel):
    name: str
    description: Optional[str] = None


class AgentCreate(AgentBase):
    skill_ids: List[str] = []
    skill_names: List[str] = []  # alternative: resolve skills by name
    skill_configs: Optional[Dict[str, Dict[str, str]]] = {}
    # Agentic identity (optional at creation time)
    slug: Optional[str] = None
    avatar_url: Optional[str] = None
    capabilities: List[str] = []
    tags: List[str] = []
    # BYOA — external agents provide their own endpoint URL
    endpoint_url: Optional[str] = None
    agent_type: str = "managed"  # managed | external | openclaw


class AgentResponse(AgentBase):
    id: str
    slug: Optional[str] = None
    avatar_url: Optional[str] = None
    capabilities: List[str] = []
    tags: List[str] = []
    agent_type: str = "managed"
    status: str
    endpoint_url: Optional[str]
    version: str
    last_seen: Optional[datetime]
    created_at: datetime
    owner_id: Optional[str]
    
    model_config = ConfigDict(from_attributes=True)


class AgentDetailResponse(AgentResponse):
    skills: List[AgentSkillResponse] = []


class AgentRegistrationResponse(HiveBaseModel):
    agent_id: str
    api_key: str
    health_check_endpoint: str
    health_check_token: str
    status: str


class AgentProfileUpdate(HiveBaseModel):
    """Allowed fields for agent self-update. Prevents setting privileged fields."""
    name: Optional[str] = None
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    capabilities: Optional[List[str]] = None
    tags: Optional[List[str]] = None


class AgentHeartbeatResponse(HiveBaseModel):
    status: str
    message: str


# ============== Health Check ==============

class HealthCheckResponse(HiveBaseModel):
    status: str
    token: str
    agent_id: str
    skills: List[str]


# ============== Filters ==============

class AgentFilter(HiveBaseModel):
    status: Optional[str] = None
    skill_id: Optional[str] = None
    owner_id: Optional[str] = None
    search: Optional[str] = None


# ============== Agent Invite Schemas ==============

class AgentInviteCreate(HiveBaseModel):
    agent_name: Optional[str] = None
    agent_type: str = "BYOA_CUSTOM"


class AgentInviteResponse(HiveBaseModel):
    invite_id: str
    invite_token: str
    expires_at: datetime
    instructions_url: str
    

class AgentAcceptInvite(HiveBaseModel):
    invite_token: str
    name: str
    description: Optional[str] = None
    endpoint_url: str
    capabilities: List[str] = []
    tags: List[str] = []
    skill_names: List[str] = []


# ============== Wallet & Transaction Schemas ==============

class WalletResponse(HiveBaseModel):
    id: str
    user_id: str
    balance: float
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class TransactionCreate(HiveBaseModel):
    target_agent_id: str
    amount: float
    task_description: str
    max_tokens: Optional[float] = None
    callback_url: Optional[str] = None
    timeout_seconds: int = 300


class TransactionResponse(HiveBaseModel):
    id: str
    from_wallet_id: str
    to_wallet_id: str
    amount: float
    transaction_type: str
    delegating_agent_id: Optional[str]
    executing_agent_id: Optional[str]
    task_description: Optional[str]
    status: str
    created_at: datetime
    completed_at: Optional[datetime]
    
    model_config = ConfigDict(from_attributes=True)


# ============== Delegation Schemas ==============

class DelegationRequest(HiveBaseModel):
    target_agent_id: str
    task_description: str
    max_tokens: float
    callback_url: Optional[str] = None
    timeout_seconds: int = 300
    context: Optional[Dict[str, Any]] = None


class DelegationResponse(HiveBaseModel):
    delegation_id: str
    status: str
    message: str


class DelegationComplete(HiveBaseModel):
    result: Dict[str, Any]
    tokens_used: float


# ============== Review Schemas ==============

class AgentReviewCreate(HiveBaseModel):
    agent_id: str
    delegation_id: str
    rating: int
    comment: Optional[str] = None
    
    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v: int) -> int:
        if not 1 <= v <= 5:
            raise ValueError("Rating must be between 1 and 5")
        return v


class AgentReviewResponse(HiveBaseModel):
    id: str
    agent_id: str
    reviewer_user_id: str
    rating: int
    comment: Optional[str]
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ============== Marketplace Schemas ==============

class MarketplaceAgentCard(HiveBaseModel):
    id: str
    name: str
    slug: Optional[str]
    avatar_url: Optional[str]
    marketplace_description: Optional[str]
    pricing_model: Optional[Dict[str, Any]]
    tags: List[str]
    status: str
    owner_id: Optional[str]
    last_seen: Optional[datetime]
    average_rating: Optional[float] = None
    total_reviews: int = 0
    
    model_config = ConfigDict(from_attributes=True)


class VisibilityUpdate(HiveBaseModel):
    is_public: bool
    marketplace_description: Optional[str] = None
    pricing_model: Optional[Dict[str, Any]] = None
