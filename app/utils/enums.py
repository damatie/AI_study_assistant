import enum


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    cancelled = "cancelled"
    expired = "expired"

class TransactionStatus(str, enum.Enum):
    pending = "pending"
    success = "success"
    failed  = "failed"
    expired = "expired"
    canceled = "canceled"


class PaymentProvider(str, enum.Enum):
    stripe = "stripe"
    paystack = "paystack"


class TransactionStatusReason(str, enum.Enum):
    # Pending lifecycle
    awaiting_payment = "awaiting_payment"
    awaiting_webhook = "awaiting_webhook"
    # System lifecycle
    ttl_elapsed = "ttl_elapsed"           # pending auto-expired due to TTL
    superseded = "superseded"             # replaced by a newer attempt
    # Failure/cancel lifecycle
    provider_failed = "provider_failed"
    user_cancelled = "user_cancelled"


class MaterialStatus(str, enum.Enum):
    idle = "idle"
    processing = "processing"
    completed  = "completed"
    failed     = "failed"


class FlashCardStatus(str, enum.Enum):
    processing = "processing"
    completed  = "completed"
    failed     = "failed"


class BillingInterval(str, enum.Enum):
    month = "month"
    year = "year"


class TransactionType(str, enum.Enum):
    initial = "initial"
    recurring = "recurring"
    refund = "refund"