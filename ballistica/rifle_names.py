"""Rifle names built from caliber and barrel length.

Rick, 2026-11-16: "When I add a rifle, suggest a name built from its caliber
and barrel length, like '338 24-inch'. If another rifle already has the same
caliber and length, add the action type or maker to tell them apart, like
'556 14.5-inch AR' or '338 24-inch Ruger'. ... That way the caliber and
barrel length questions always match the names."

Pure helpers. The conversations that use them (the new-rifle interview and
the rename walk-through) live in cli.py.
"""
from __future__ import annotations

import re

from .narrowing import _CALIBER_WORDS, rifle_barrel
from .profiles import Rifle

# Trailing words that only say who standardized the cartridge: ".308
# Winchester" is "308", "5.56 NATO" is "556", ".338 Lapua Magnum" is "338".
# "300 Win Mag" keeps its words, since "300" alone would also be 300 Blackout.
_DROP_TRAILING = {"nato", "winchester", "win", "remington", "rem", "lapua"}
_LAPUA_MAG_RE = re.compile(r"\blapua\s+mag(?:num)?\b", re.IGNORECASE)
_BARE_DOTTED_RE = re.compile(r"^\d\.\d\d$")      # 5.56, 7.62 -- but not 6.5
_BARREL_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)?(?:-?(?:inch(?:es)?|in)|\")?$", re.IGNORECASE)
_NAME_FILLER = {"the", "a", "an", "my", "rifle", "gun", "barrel", "inch", "inches", "in"}

_NAME_LEAD_RE = re.compile(
    r"^(?:(?:no|nope|actually|okay|ok|well|um|uh)[,.]?\s+)*"
    r"(?:(?:let'?s|i'?d|i want to|i wanna|we'll|you can)\s+)?"
    r"(?:(?:call (?:it|this(?: one)?|this rifle)|name (?:it|this(?: one)?)|make it|go with|use|how about|"
    r"i want|it'?s called|it'?s)\s+)?",
    re.IGNORECASE,
)
_MAKER_LEAD_RE = re.compile(
    r"^(?:(?:it'?s|it is|that'?s|that one'?s)\s+)?(?:an?\s+|the\s+)?(?:made by\s+|by\s+)?",
    re.IGNORECASE,
)
_MAKER_TAIL_RE = re.compile(r"\s+(?:rifle|action|one|gun)$", re.IGNORECASE)


def short_caliber(caliber: str) -> str:
    """'.338 Lapua Magnum' -> '338', '5.56 NATO' -> '556', '.308 Winchester'
    -> '308', '6.5 Creedmoor' -> '6.5 Creedmoor', '300 Blackout' -> '300 Blackout'."""
    text = _LAPUA_MAG_RE.sub("Lapua", (caliber or "").strip())
    words = text.split()
    while len(words) > 1 and words[-1].lower().strip(".,") in _DROP_TRAILING:
        words.pop()
    if not words:
        return ""
    words[0] = words[0].lstrip(".")
    if len(words) == 1 and _BARE_DOTTED_RE.match(words[0]):
        words[0] = words[0].replace(".", "")
    return " ".join(words)


def barrel_label(barrel: float | None) -> str:
    return f"{float(barrel):g}-inch" if barrel else ""


def name_base(caliber: str, barrel: float | None) -> str | None:
    """'338 24-inch' -- or None without a caliber to build it from."""
    cal = short_caliber(caliber)
    if not cal:
        return None
    return f"{cal} {barrel_label(barrel)}".strip()


def _rifle_base(rifle: Rifle) -> str | None:
    return name_base(rifle.caliber, rifle_barrel(rifle)) if rifle.caliber.strip() else None


def _same_kind(rifle: Rifle, base: str) -> bool:
    """Whether this rifle has the same caliber and barrel length the base names."""
    own = _rifle_base(rifle)
    if own is not None:
        return own.lower() == base.lower()
    name = rifle.name.lower()
    return name == base.lower() or name.startswith(base.lower() + " ")


def needs_maker(caliber: str, barrel: float | None, rifles: list[Rifle], exclude: str | None = None) -> bool:
    """Whether another saved rifle has the same caliber and barrel length."""
    base = name_base(caliber, barrel)
    return bool(base) and any(r.name != exclude and _same_kind(r, base) for r in rifles)


def suggest_name(caliber: str, barrel: float | None, rifles: list[Rifle], maker: str = "",
                 exclude: str | None = None) -> str | None:
    """'338 24-inch', or '338 24-inch Ruger' when another rifle has the same
    caliber and length. Never a name another rifle already has (a number is
    added as a last resort). None without a caliber."""
    base = name_base(caliber, barrel)
    if not base:
        return None
    others = [r for r in rifles if r.name != exclude]
    name = f"{base} {maker}".strip() if maker and any(_same_kind(r, base) for r in others) else base
    taken = {r.name.lower() for r in others}
    candidate, n = name, 2
    while candidate.lower() in taken:
        candidate = f"{name} {n}"
        n += 1
    return candidate


def maker_from_name(name: str) -> str:
    """The first word of an old name that isn't about the caliber or the
    barrel: 'Tikka T3x 6.5 Creedmoor' -> 'Tikka', 'AR-15 20in Faxon' -> 'AR-15'."""
    for token in name.split():
        word = token.strip(".,()'\"")
        low = word.lower()
        if not word or not re.search(r"[a-z]", low):
            continue
        if _BARREL_TOKEN_RE.match(word) or low in _NAME_FILLER or low in _CALIBER_WORDS:
            continue
        if re.match(r"^\.?\d", word):
            continue   # '6.5', '.223', '9mm', '7.62x39' -- caliber numbers
        return word[0].upper() + word[1:] if word.islower() else word
    return ""


def clean_spoken_name(text: str) -> str:
    """What he wants it called, from 'call it the Big Bertha.' -> 'Big Bertha'."""
    name = _NAME_LEAD_RE.sub("", text.strip())
    name = re.sub(r"^the\s+", "", name, flags=re.IGNORECASE)
    return name.strip(" \t.,!?\"'")


def clean_maker(text: str) -> str:
    """'it's a Ruger' -> 'Ruger', 'an AR' -> 'AR', 'tikka' -> 'Tikka'."""
    maker = _MAKER_LEAD_RE.sub("", text.strip().strip(".,!?\"'"))
    maker = _MAKER_TAIL_RE.sub("", maker).strip(" .,!?\"'")
    words = []
    for w in maker.split():
        if len(w) <= 2 and w.isalpha():
            words.append(w.upper())          # 'ar' -> 'AR'
        elif w.islower():
            words.append(w[0].upper() + w[1:])
        else:
            words.append(w)
    return " ".join(words)


# Renaming one rifle by voice (2026-11-24): "rename my 6mm ARC to ARC 18 inch
# Bergara". A name that doesn't say the caliber ("Long Tom", "my gun") is
# read back with the caliber-barrel-maker name offered beside it, and that
# same name is what "suggest a name" gives.
_INCH_WORDS = {"inch", "inches", "in", '"'}


def mentions_caliber(name: str) -> bool:
    """Whether a name says the caliber: a caliber word ('Creedmoor', 'ARC')
    or a caliber number ('6mm', '308', '6.5') -- not a barrel length."""
    tokens = [t.strip(".,()'\"") for t in name.split()]
    for i, tok in enumerate(tokens):
        low = tok.lower()
        if not low:
            continue
        if low in _CALIBER_WORDS - {"x", "short", "long", "super", "special", "auto", "carbine", "mm", "cm"}:
            return True
        nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
        if re.search(r"(?:-?inch(?:es)?|-in|\")$", low) or nxt in _INCH_WORDS:
            continue   # '18-inch', '18 inch': a barrel length
        if re.match(r"^\.?\d{2,}|^\d+\.\d+|^\d+(?:mm|x\d)", low):
            return True
    return False


def scheme_name(rifle: Rifle, rifles: list[Rifle], maker: str = "") -> str | None:
    """Caliber, barrel length and maker: '6mm ARC 18-inch Bergara' -- the
    maker always included when it's known, unlike suggest_name(). None
    without a saved caliber. Never a name another rifle already has."""
    base = name_base(rifle.caliber, rifle_barrel(rifle)) if rifle.caliber.strip() else None
    if not base:
        return None
    name = f"{base} {maker}".strip()
    taken = {r.name.lower() for r in rifles if r.name != rifle.name}
    candidate, n = name, 2
    while candidate.lower() in taken:
        candidate = f"{name} {n}"
        n += 1
    return candidate
