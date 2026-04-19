"""Persistent log entries for agent delegations.

Every message surfaced to the SSE stream (system events + agent-reported
progress) is also written here so that late SSE subscribers — or reconnects
after a network blip — can catch up without losing history.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON, Index
from database import Base


class DelegationLog(Base):
    __tablename__ = "delegation_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    delegation_id = Column(
        String(36),
        ForeignKey("transactions.id"),
        nullable=False,
        index=True,
    )
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    level = Column(String(20), nullable=False, default="info")
    message = Column(Text, nullable=False)
    data = Column(JSON, nullable=True)
    source = Column(String(20), nullable=False, default="system")  # system | agent

    __table_args__ = (
        Index("ix_delegation_logs_delegation_ts", "delegation_id", "timestamp"),
    )

    def to_event(self) -> dict:
        """Return the shape consumed by the frontend SSE client."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "message": self.message,
            "data": self.data or {},
            "source": self.source,
        }
