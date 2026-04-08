"""Agent model for AI agents registered in the marketplace."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, Enum
from sqlalchemy.orm import relationship
from database import Base
import enum


class AgentStatus(str, enum.Enum):
    PENDING = "pending"
    VERIFYING = "verifying"
    ACTIVE = "active"
    IDLE = "idle"
    OFFLINE = "offline"
    ERROR = "error"


class Agent(Base):
    __tablename__ = "agents"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    
    # Owner (nullable for autonomous agents)
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    owner = relationship("User", back_populates="agents")
    
    # Authentication
    api_key_hash = Column(String(255), nullable=False)
    
    # Status
    status = Column(String(20), default=AgentStatus.PENDING.value)
    
    # Endpoint configuration
    endpoint_url = Column(String(500), nullable=True)  # Public URL path
    internal_port = Column(Integer, nullable=True)  # Container port
    container_id = Column(String(100), nullable=True)  # Docker container ID
    
    # Health tracking
    last_seen = Column(DateTime, nullable=True)
    last_health_check = Column(DateTime, nullable=True)
    health_check_token = Column(String(100), nullable=True)
    
    # Version
    version = Column(String(50), default="1.0.0")
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    skills = relationship("AgentSkill", back_populates="agent", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Agent {self.name} ({self.status})>"
    
    def calculate_status(self):
        """Calculate current status based on last_seen."""
        if self.status == AgentStatus.ERROR.value:
            return AgentStatus.ERROR
        
        if not self.last_seen:
            if self.status == AgentStatus.PENDING.value:
                return AgentStatus.PENDING
            return AgentStatus.OFFLINE
        
        minutes_since = (datetime.utcnow() - self.last_seen).total_seconds() / 60
        
        if minutes_since < 5:
            return AgentStatus.ACTIVE
        elif minutes_since < 30:
            return AgentStatus.IDLE
        else:
            return AgentStatus.OFFLINE
