"""Brand tokens — the one place colour and canvas size are defined.

cards.py and adspot.py each used to carry their own copy of the palette,
which meant a colour could change on the card and not on the video. Both
now read content/brand_tokens.json through this module, and a test rejects
any colour literal that creeps back into either renderer.

NOTE: the values currently in brand_tokens.json are the ones that were
hardcoded in the renderers, relocated verbatim. They have NOT been
reconciled with the Nebula theme in the product — only `violet` matches
today. See the _provenance block in the tokens file.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKENS_PATH = os.path.join(ROOT, "content", "brand_tokens.json")

with open(TOKENS_PATH) as _f:
    TOKENS = json.load(_f)

COLORS = TOKENS["colors"]
ALPHA = TOKENS["alpha"]
SIZES = TOKENS["sizes"]


def rgb(value):
    """'#7c3aed' -> (124, 58, 237). Accepts a token name or a hex string."""
    hex_value = COLORS.get(value, value).lstrip("#")
    return tuple(int(hex_value[i:i + 2], 16) for i in (0, 2, 4))


def rgba(value, alpha):
    """As rgb(), with an alpha channel appended."""
    return rgb(value) + (int(alpha),)


def size(name):
    """Named canvas size -> (width, height)."""
    return tuple(SIZES[name])
