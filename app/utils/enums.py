import enum


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    cancelled = "cancelled"
    expired = "expired"

class TransactionStatus(str, enum.Enum):
    pending = "pending"
    success = "success"
    failed  = "failed"


class MaterialStatus(str, enum.Enum):
    idle = "idle"
    processing = "processing"
    completed  = "completed"
    failed     = "failed"