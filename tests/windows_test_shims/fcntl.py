"""Windows-only test shim for Home Assistant's single-instance lock import."""

LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8


def flock(_fd: int, _operation: int) -> None:
    """Tests do not launch a second Home Assistant process, so no OS lock is needed."""
