"""Transaction model for agent-to-agent payments."""
import uuid
import enum
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Numeric, Integer, JSON
from sqlalchemy.orm import relationship
from database import Base


class TransactionType(str, enum.Enum):
    DELEGATION = "delegation"
    PAYMENT = "payment"
    REFUND = "refund"
    ADMIN_GRANT = "admin_grant"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    from_wallet_id = Column(String(36), ForeignKey("wallets.id"), nullable=False)
    to_wallet_id = Column(String(36), ForeignKey("wallets.id"), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    # Amount collected by the platform (taken out of agent's share on settlement)
    platform_fee = Column(Numeric(10, 4), nullable=True, default=0)
    transaction_type = Column(String(20), nullable=False)

    # Delegation chain tracking
    delegating_agent_id = Column(String(36), ForeignKey("agents.id"), nullable=True)
    executing_agent_id = Column(String(36), ForeignKey("agents.id"), nullable=True)
    # The human user who originally triggered the chain (threads through A2A hops)
    originating_user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    # Session groups related delegations together (multi-turn workflows)
    session_id = Column(String(36), nullable=True, index=True)
    # How deep in an agent-to-agent chain this task sits (0 = direct human request)
    delegation_depth = Column(Integer, nullable=False, default=0)

    task_description = Column(Text, nullable=True)
    # Structured result returned by the executing agent
    task_result = Column(JSON, nullable=True)

    status = Column(String(20), default=TransactionStatus.PENDING.value)
    # Why the task was refunded/failed (for reputation scoring)
    refund_reason = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    from_wallet = relationship("Wallet", foreign_keys=[from_wallet_id])
    to_wallet = relationship("Wallet", foreign_keys=[to_wallet_id])
    delegating_agent = relationship("Agent", foreign_keys=[delegating_agent_id])
    executing_agent = relationship("Agent", foreign_keys=[executing_agent_id])

    def __repr__(self):
        return f"<Transaction {self.id} {self.amount} tokens ({self.status})>"
