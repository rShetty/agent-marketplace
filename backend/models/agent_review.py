"""Agent review model for reputation and trust system."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base


class AgentReview(Base):
    __tablename__ = "agent_reviews"
    __table_args__ = (
        UniqueConstraint('delegation_id', name='uq_delegation_review'),
    )
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String(36), ForeignKey("agents.id"), nullable=False)
    reviewer_user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    delegation_id = Column(String(36), ForeignKey("transactions.id"), nullable=False)
    rating = Column(Integer, nullable=False)  # 1-5 stars
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    agent = relationship("Agent")
    reviewer = relationship("User")
    delegation = relationship("Transaction")
    
    def __repr__(self):
        return f"<AgentReview agent={self.agent_id} rating={self.rating}>"
