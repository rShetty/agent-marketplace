"""Transaction model for agent-to-agent payments."""
import uuid
import enum
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Numeric, Enum
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
    transaction_type = Column(String(20), nullable=False)
    delegating_agent_id = Column(String(36), ForeignKey("agents.id"), nullable=True)
    executing_agent_id = Column(String(36), ForeignKey("agents.id"), nullable=True)
    task_description = Column(Text, nullable=True)
    status = Column(String(20), default=TransactionStatus.PENDING.value)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    # Relationships
    from_wallet = relationship("Wallet", foreign_keys=[from_wallet_id])
    to_wallet = relationship("Wallet", foreign_keys=[to_wallet_id])
    delegating_agent = relationship("Agent", foreign_keys=[delegating_agent_id])
    executing_agent = relationship("Agent", foreign_keys=[executing_agent_id])
    
    def __repr__(self):
        return f"<Transaction {self.id} {self.amount} tokens ({self.status})>"
