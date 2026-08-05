"""TSAgent public package surface."""

__all__ = ["TSAgent", "SessionRuntime"]


def __getattr__(name: str):
    if name in {"TSAgent", "SessionRuntime"}:
        from agent.api import SessionRuntime, TSAgent

        return {"TSAgent": TSAgent, "SessionRuntime": SessionRuntime}[name]
    raise AttributeError(name)
