"""How far a character's context reaches, shared by every crop and glyph that shows one."""
from __future__ import annotations

#: How far a context reaches past the character, in character sizes, across and along the line: five
#: neighbours above and below in a vertical column, and three columns to each side.
CONTEXT_REACH = (3, 5)


def reach(x: float, y: float, w: float, h: float, width: int, height: int,
          margins: tuple[float, float] = CONTEXT_REACH) -> tuple[int, int, int, int]:
    """The (left, top, right, bottom) window `margins` character sizes around a box, inside the
    `width` × `height` image it is drawn on."""
    size = max(w, h)
    mx, my = size * margins[0], size * margins[1]
    return (max(0, int(x - mx)), max(0, int(y - my)), min(width, int(x + w + mx)), min(height, int(y + h + my)))
