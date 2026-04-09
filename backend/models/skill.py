"""Skill model for agent capabilities."""
import uuid
from sqlalchemy import Column, String, Text, JSON
from sqlalchemy.orm import relationship
from database import Base


class Skill(Base):
    __tablename__ = "skills"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(50), unique=True, nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)
    
    # Tier: core (no auth), connected (needs user auth), premium (future)
    tier = Column(String(20), default="core")
    
    # Category for grouping
    category = Column(String(50), default="general")
    
    # Required environment variables for connected skills
    # e.g., ["GITHUB_TOKEN", "LINEAR_API_KEY"]
    required_env_vars = Column(JSON, default=list)
    
    is_active = Column(String(10), default="true")  # Stored as string for SQLite compat
    
    # Relationships
    agent_skills = relationship("AgentSkill", back_populates="skill")
    
    def __repr__(self):
        return f"<Skill {self.name}>"
