"""Generated robot avatars that tell agents apart in discussions.

A name joins a colour, eyes and a top, such as ``teal-visor-antenna``. The browser
draws each part; frontend/src/Avatar.tsx keeps the same lists in the same order.
Names are stored on agent identities, so only append parts; never rename or remove one.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Iterable

COLORS = ("red", "orange", "amber", "lime", "green", "teal", "cyan", "sky", "blue", "indigo", "violet", "pink")
EYES = ("dots", "visor", "cyclops", "sleepy", "square")
TOPS = ("antenna", "twin", "ears", "bare")
AVATARS = tuple(f"{color}-{eyes}-{top}" for color in COLORS for eyes in EYES for top in TOPS)
_KNOWN = frozenset(AVATARS)


def is_avatar(name: object) -> bool:
    return isinstance(name, str) and name in _KNOWN


def choose(taken: Iterable[str], rng: random.Random | None = None) -> str:
    """The least-used avatar, preferring a colour nobody shows yet; ties are random.

    Only avatars still in use count, so a retired agent's avatar is recycled.
    """
    taken = list(taken)
    uses = Counter(taken)
    colors = Counter(name.split("-", 1)[0] for name in taken)
    draw = rng or random
    return min(AVATARS, key=lambda name: (uses[name], colors[name.split("-", 1)[0]], draw.random()))
