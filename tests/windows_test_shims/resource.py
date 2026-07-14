"""Windows compatibility shim for Home Assistant's POSIX file-limit helper."""

RLIMIT_NOFILE = 7


def getrlimit(_resource: int) -> tuple[int, int]:
    """Report a stable no-op limit used only during Home Assistant startup imports."""
    return (4096, 4096)


def setrlimit(_resource: int, _limits: tuple[int, int]) -> None:
    """Windows has no compatible POSIX call; tests require no state change."""
