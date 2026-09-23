"""ANSI colour helpers shared by the bytecode rendering tools."""

_ANSI_CYAN = "\033[36m"
_ANSI_YELLOW = "\033[33m"
_ANSI_GREEN = "\033[32m"
_ANSI_GREY = "\033[90m"
_ANSI_RESET = "\033[0m"


def cyan(text: str, color: bool) -> str:
    """Wrap text in cyan ANSI codes if color is enabled."""
    return f"{_ANSI_CYAN}{text}{_ANSI_RESET}" if color else text


def yellow(text: str, color: bool) -> str:
    """Wrap text in yellow ANSI codes if color is enabled."""
    return f"{_ANSI_YELLOW}{text}{_ANSI_RESET}" if color else text


def green(text: str, color: bool) -> str:
    """Wrap text in green ANSI codes if color is enabled."""
    return f"{_ANSI_GREEN}{text}{_ANSI_RESET}" if color else text


def grey(text: str, color: bool) -> str:
    """Wrap text in grey ANSI codes if color is enabled."""
    return f"{_ANSI_GREY}{text}{_ANSI_RESET}" if color else text
