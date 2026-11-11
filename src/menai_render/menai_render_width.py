"""
Line-width clipping shared by the command-line rendering tools.

Renderers produce complete lines so that no information is lost by default.
A tool that wants to fit its output to a terminal passes the chosen width here
at the point of printing, and every line longer than that width is clipped.

Clipping counts visible characters, not ANSI colour escapes: a coloured line is
truncated at the requested column and then reset, so clipping cannot leave a
terminal stuck in a colour.
"""

import re

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_ANSI_RESET = "\x1b[0m"


def _visible_length(text: str) -> int:
    """Return the number of characters visible after ANSI escapes are removed."""
    return len(_ANSI_ESCAPE.sub("", text))


def clip(text: str, width: int | None) -> str:
    """
    Clip a line to at most `width` visible characters.

    `None` means no restriction; the line is returned unchanged.  A width of
    zero or less is treated as no restriction so that an unset or nonsensical
    value can never empty the output silently.  ANSI colour escapes do not count
    towards the width, and a clipped coloured line is reset at the cut point.
    """
    if width is None or width <= 0 or _visible_length(text) <= width:
        return text

    pieces: list[str] = []
    visible = 0
    index = 0

    while index < len(text):
        escape = _ANSI_ESCAPE.match(text, index)
        if escape is not None:
            pieces.append(escape.group(0))
            index = escape.end()
            continue

        if visible >= width:
            break

        pieces.append(text[index])
        visible += 1
        index += 1

    if _ANSI_ESCAPE.search(text):
        pieces.append(_ANSI_RESET)

    return "".join(pieces)


def clip_lines(lines: list[str], width: int | None) -> list[str]:
    """Clip every line in a list to at most `width` visible characters."""
    if width is None or width <= 0:
        return lines

    return [clip(line, width) for line in lines]
