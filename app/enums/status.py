from enum import Enum


class VaultStatusEnum(str, Enum):
    """Статусы vault"""

    CREATING = "creating"
    AVAILABLE = "available"
    ERROR = "error"
    MAINTENANCE = "maintenance"


class TransferStatus(str, Enum):
    """Status for outgoing transfers (internal + external)."""
    
    PENDING_APPROVAL = "pending_approval"   # Waiting for Workflow approval (external only)
    PENDING_BALANCE = "pending_balance"     # Approved, waiting for HOT wallet balance
    PENDING = "pending"                     # Balance reserved, ready for signing
    SIGNING = "signing"                     # Sent to Signer for signing
    BROADCASTING = "broadcasting"           # Transaction broadcasted to blockchain
    COMPLETED = "completed"                 # Successfully completed
    REJECTED = "rejected"                   # Rejected by Workflow
    FAILED = "failed"                       # Technical error
    CANCELLED = "cancelled"                 # Cancelled manually


class InvoiceStatusEnum(str, Enum):
    """Статусы заявок"""

    PENDING = "pending"
    PAID = "paid"
    CONFIRMING = "confirming"
    DETECTED = "detected"
    CANCELED = "canceled"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REORG = "reorg"
