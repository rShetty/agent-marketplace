"""Pydantic schemas for API requests/responses."""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, EmailStr
from datetime import datetime


# ============== User Schemas ==============

class UserBase(BaseModel):
    email: EmailStr
    name: str


class UserCreate(UserBase):
    password: str


class UserResponse(UserBase):
    id: str
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    name: Optional[str] = None
    model_api_keys: Optional[Dict[str, str]] = None


# ============== Auth Schemas ==============

class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    sub: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ============== Skill Schemas ==============

class SkillBase(BaseModel):
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
    is_active: str
    
    class Config:
        from_attributes = True


# ============== Agent Schemas ==============

class AgentBase(BaseModel):
    name: str
    description: Optional[str] = None


class AgentCreate(AgentBase):
    skill_ids: List[str] = []
    skill_configs: Optional[Dict[str, Dict[str, str]]] = {}


class AgentResponse(AgentBase):
    id: str
    status: str
    endpoint_url: Optional[str]
    version: str
    last_seen: Optional[datetime]
    created_at: datetime
    owner_id: Optional[str]
    
    class Config:
        from_attributes = True


class AgentDetailResponse(AgentResponse):
    skills: List["AgentSkillResponse"] = []


class AgentRegistrationResponse(BaseModel):
    agent_id: str
    api_key: str
    health_check_endpoint: str
    health_check_token: str
    status: str


class AgentHeartbeatResponse(BaseModel):
    status: str
    message: str


# ============== AgentSkill Schemas ==============

class AgentSkillResponse(BaseModel):
    id: str
    skill: SkillResponse
    config: Optional[Dict[str, Any]] = None
    added_at: datetime
    
    class Config:
        from_attributes = True


class AgentSkillCreate(BaseModel):
    skill_id: str
    config: Optional[Dict[str, str]] = None


# ============== Health Check ==============

class HealthCheckResponse(BaseModel):
    status: str
    token: str
    agent_id: str
    skills: List[str]


# ============== Filters ==============

class AgentFilter(BaseModel):
    status: Optional[str] = None
    skill_id: Optional[str] = None
    owner_id: Optional[str] = None
    search: Optional[str] = None
