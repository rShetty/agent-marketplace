"""Agent invite model for BYOA registration."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class AgentInvite(Base):
    __tablename__ = "agent_invites"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    invite_token = Column(String(64), unique=True, nullable=False, index=True)
    agent_name = Column(String(255), nullable=True)
    agent_type = Column(String(20), default="BYOA_CUSTOM")
    status = Column(String(20), default="pending")  # pending, used, expired
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    agent_id = Column(String(36), ForeignKey("agents.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User")
    agent = relationship("Agent")
    
    def __repr__(self):
        return f"<AgentInvite {self.invite_token} ({self.status})>"
