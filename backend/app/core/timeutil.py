from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware UTC now. All persisted timestamps are UTC; facilities localise."""
    return datetime.now(UTC)
