"""Static reference fixture for CONTINUE_REFERENCE case cont-ref-01."""


def parse(value: str) -> list[str]:
    """Split a comma-separated value into trimmed fields."""
    return [item.strip() for item in value.split(",") if item.strip()]
