"""Compatibility imports for benchmark-only callers.

Production code owns these input guards under ``understanding`` and must not
depend on this benchmark namespace.
"""

from ..understanding.input_guard import InputLengthGuard, InputLengthPolicy

__all__ = ["InputLengthGuard", "InputLengthPolicy"]
