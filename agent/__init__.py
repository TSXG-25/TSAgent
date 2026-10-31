"""TSAgent public package surface."""

__all__ = ["TSAgent"]


def __getattr__(name: str):
    if name == "TSAgent":
        from agent.api import TSAgent

        return TSAgent
    raise AttributeError(name)
