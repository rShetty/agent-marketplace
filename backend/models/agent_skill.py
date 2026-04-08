"""Junction table for agent-skill relationships."""
import uuid
from sqlalchemy import Column, String, ForeignKey, JSON, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class AgentSkill(Base):
    __tablename__ = "agent_skills"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String(36), ForeignKey("agents.id"), nullable=False)
    skill_id = Column(String(36), ForeignKey("skills.id"), nullable=False)
    
    # Configuration for connected skills (encrypted env vars)
    config = Column(JSON, default=dict)
    
    added_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    agent = relationship("Agent", back_populates="skills")
    skill = relationship("Skill", back_populates="agent_skills")
    
    def __repr__(self):
        return f"<AgentSkill {self.agent_id}:{self.skill_id}>"
