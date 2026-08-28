"""Static reference fixture for CONTINUE_REFERENCE case cont-ref-09."""


def save(path: str, content: str) -> str:
    """Return the destination marker without touching the real filesystem."""
    return f"{path}:{content}"
