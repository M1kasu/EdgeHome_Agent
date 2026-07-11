"""EdgeHome tool registry package.

The executor imports ``tools.registry`` while the optional runtime facade imports
the executor.  Keep the facade lazy here so importing a registry cannot create a
circular import while Home Assistant loads the conversation platform.
"""

from typing import Any

__all__ = ["EdgeHomeToolRuntime", "ToolResult"]


def __getattr__(name: str) -> Any:
    """Lazily expose the optional verified runtime facade without import cycles."""
    if name in __all__:
        from .runtime import EdgeHomeToolRuntime, ToolResult

        return {"EdgeHomeToolRuntime": EdgeHomeToolRuntime, "ToolResult": ToolResult}[name]
    raise AttributeError(name)
