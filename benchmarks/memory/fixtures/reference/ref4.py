"""Static reference fixture for CONTINUE_REFERENCE case cont-ref-04."""


def build(name: str) -> dict[str, str]:
    """Build a deterministic record."""
    return {"name": name, "kind": "fixture"}
