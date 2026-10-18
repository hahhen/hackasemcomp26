"""LangChain + LangGraph tool-calling agent package.

Imports are lazy so that ``python -m agent.<module>`` does not pull in every
submodule before the target module runs (which triggers a runpy warning).
"""

__all__ = ["build_agent"]


def __getattr__(name: str):
    if name == "build_agent":
        from agent.graph import build_agent

        return build_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
