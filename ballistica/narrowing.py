"""Picking one rifle or load out loud without reading a long list.

Rick, 2026-11-16: "Instead of listing rifles, she should ask 'What caliber
are you shooting?', then if more than one rifle matches, 'What barrel
length?', then confirm the one rifle that fits. She should never read a list
longer than three or four."

These are pure helpers: they look at what was said and the rifles (or loads)
still in the running, and either narrow them down or say what to ask next.
The conversation itself (who asked what, when to confirm) lives in cli.py.
"""
from __future__ import annotations

import re

from .profiles import Load, Rifle

# The longest list she reads aloud. Anything longer gets a narrowing question.
MAX_SPOKEN_LIST = 4

_NUM_RE = re.compile(r"\d*\.?\d+")
_WORD_RE = re.compile(r"[a-z]+")
# "24 inch", "24-inch", "24in", '24"', "24 inches"
_BARREL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*-?\s*(?:inch(?:es)?\b|in\b|\")")
# Words that describe a cartridge rather than one particular rifle. "Creedmoor"
# names a caliber, so it narrows by caliber; "Tikka" names the rifle itself.
_CALIBER_WORDS = {
    "creedmoor", "creed", "blackout", "blk", "nato", "wylde", "win", "winchester", "mag", "magnum",
    "prc", "arc", "grendel", "remington", "rem", "spc", "lapua", "swift", "hornet", "mm", "cm",
    "saum", "wsm", "wssm", "weatherby", "wby", "valkyrie", "acp", "auto", "luger", "parabellum",
    "special", "spl", "lr", "hmr", "wmr", "sp", "whisper", "bmg", "springfield", "mauser", "carbine",
    "magnum", "super", "short", "long", "rimfire", "x",
}
# Words in an answer that say nothing about which rifle.
_FILLER = {
    "the", "a", "an", "my", "i", "im", "i'm", "am", "its", "it's", "it", "is", "one", "with", "and",
    "shooting", "shoot", "using", "use", "today", "rifle", "gun", "caliber", "calibre", "barrel",
    "inch", "inches", "in", "that", "this", "on", "of", "uh", "um", "umm", "so", "well", "okay", "ok",
    "yes", "yeah", "no", "got", "have", "running", "run", "going", "with", "please", "ballistica",
    "what", "which", "for", "to", "be", "just", "like", "think", "about", "load", "ammo",
    "ammunition", "grain", "grains", "gr", "bullet", "weight", "length", "long", "short",
}
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
}


def short_list(items: list[str], last_word: str = "or") -> str | None:
    """'a', 'a or b', 'a, b or c' -- or None when there are too many to read aloud."""
    items = list(items)
    if not items or len(items) > MAX_SPOKEN_LIST:
        return None
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {last_word} " + items[-1]


def _spoken_numbers(text: str) -> str:
    """'twenty four inch' -> '24 inch' (speech-to-text usually writes digits
    already; this covers the times it doesn't)."""
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i].strip(",.?!")
        if w in _NUMBER_WORDS and w != "one" and _NUMBER_WORDS[w] >= 2:
            value = _NUMBER_WORDS[w]
            nxt = words[i + 1].strip(",.?!") if i + 1 < len(words) else ""
            if value in (20, 30) and nxt in _NUMBER_WORDS and 1 <= _NUMBER_WORDS[nxt] <= 9:
                value += _NUMBER_WORDS[nxt]
                i += 1
            out.append(str(value))
        else:
            out.append(words[i])
        i += 1
    return " ".join(out)


def _num_key(token: str) -> str:
    """'6.5' and '65', '.223' and '223', '5.56' and '556' compare equal."""
    return token.replace(".", "").lstrip("0") or "0"


def rifle_barrel(rifle: Rifle) -> float | None:
    """The saved barrel length, or one written into the name ('AR-15 20-inch Faxon')."""
    if rifle.barrel_length_in:
        return float(rifle.barrel_length_in)
    m = _BARREL_RE.search(rifle.name.lower())
    return float(m.group(1)) if m else None


def _caliber_text(rifle: Rifle) -> str:
    return (rifle.caliber or rifle.name).lower()


def _caliber_tokens(text: str) -> tuple[set[str], set[str]]:
    """(number keys, caliber words) in a caliber -- or in a spoken answer, with
    any barrel length taken out first so '24 inch' isn't read as a caliber."""
    text = _BARREL_RE.sub(" ", text.lower())
    numbers = {_num_key(n) for n in _NUM_RE.findall(text)}
    words = {w for w in _WORD_RE.findall(text) if w in _CALIBER_WORDS and w != "x"}
    return numbers, words


def rifle_spoken(rifle: Rifle) -> str:
    """How she names one rifle to confirm it: 'the 6.5 Creedmoor Tikka, 24-inch barrel'."""
    name = rifle.name
    label = name
    cal = rifle.caliber.strip()
    if cal:
        cal_nums, cal_words = _caliber_tokens(cal)
        name_nums, name_words = _caliber_tokens(name)
        # A name that already carries the caliber's number ('338 24-inch' for
        # a .338 Lapua Magnum) isn't repeated with the full caliber in front.
        covered = cal_nums <= name_nums if cal_nums else cal_words <= name_words
        if not covered:
            label = f"{cal} {name}"
    barrel = rifle_barrel(rifle)
    if rifle.barrel_length_in and not _BARREL_RE.search(name.lower()):
        label += f", {barrel:g}-inch barrel"
    return f"the {label}"


def confirm_question(rifle: Rifle) -> str:
    """'The 6.5 Creedmoor Tikka, 24-inch barrel -- right?'"""
    spoken = rifle_spoken(rifle)
    return f"{spoken[0].upper()}{spoken[1:]} -- right?"


def distinct_name_hits(rifles: list[Rifle], text: str) -> list[Rifle]:
    """Rifles named by a word that isn't about the caliber ('the Tikka')."""
    answer = {w for w in _WORD_RE.findall(text.lower())
              if len(w) >= 3 and w not in _FILLER and w not in _CALIBER_WORDS}
    if not answer:
        return []
    hits = []
    for r in rifles:
        name_words = {w for w in _WORD_RE.findall(r.name.lower())
                      if len(w) >= 3 and w not in _CALIBER_WORDS}
        if any(a == n or (len(a) >= 4 and (n.startswith(a) or a.startswith(n))) for a in answer for n in name_words):
            hits.append(r)
    return hits


def _by_barrel(rifles: list[Rifle], length: float) -> list[Rifle]:
    return [r for r in rifles if rifle_barrel(r) is not None and abs(rifle_barrel(r) - length) < 0.6]


def narrow_rifles(rifles: list[Rifle], text: str, expect_barrel: bool = False) -> tuple[list[Rifle], bool]:
    """The rifles that fit what was said, and whether anything usable was
    said at all (a name word, a caliber, or a barrel length). With nothing
    usable the rifles come back unchanged; with something that fits none of
    them, an empty list comes back."""
    text = _spoken_numbers(text.lower())
    heard = False
    pool = list(rifles)

    named = distinct_name_hits(pool, text)
    if named:
        heard = True
        pool = named

    nums, words = _caliber_tokens(text)
    barrel_m = _BARREL_RE.search(text)
    barrel = float(barrel_m.group(1)) if barrel_m else None
    if barrel is None and expect_barrel and len(nums) == 1 and not words:
        # Just asked the barrel length: a bare number is a length if one fits.
        raw = float(_NUM_RE.findall(_BARREL_RE.sub(" ", text))[0])
        if _by_barrel(pool, raw):
            barrel, nums = raw, set()

    if nums or words:
        heard = True
        scored = []
        for r in pool:
            cal_nums, cal_words = _caliber_tokens(_caliber_text(r))
            score = len(nums & cal_nums) + sum(
                1 for w in words if any(c == w or c.startswith(w) or w.startswith(c) for c in cal_words))
            scored.append((score, r))
        best = max((s for s, _ in scored), default=0)
        if best > 0:
            pool = [r for s, r in scored if s == best]
        elif nums and not words and barrel is None and len(nums) == 1:
            # A bare number that isn't any caliber: maybe a barrel length.
            raw = float(_NUM_RE.findall(_BARREL_RE.sub(" ", text))[0])
            by_len = _by_barrel(pool, raw)
            if not by_len:
                return [], True
            pool = by_len
        else:
            return [], True

    if barrel is not None:
        heard = True
        pool = _by_barrel(pool, barrel)
    return pool, heard


def _barrels_tell_apart(rifles: list[Rifle]) -> bool:
    lengths = [rifle_barrel(r) for r in rifles]
    return all(length is not None for length in lengths) and len(set(lengths)) == len(lengths)


def _calibers_differ(rifles: list[Rifle]) -> bool:
    cals = [r.caliber.strip().lower() for r in rifles]
    return not all(cals) or len(set(cals)) > 1


def next_rifle_question(rifles: list[Rifle], asked_caliber: bool) -> tuple[str, str]:
    """For two or more rifles still in the running: (question, what it asks
    -- 'caliber', 'barrel' or 'name')."""
    if not asked_caliber and _calibers_differ(rifles):
        return "What caliber are you shooting?", "caliber"
    lengths = {rifle_barrel(r) for r in rifles}
    if None not in lengths and len(lengths) > 1:
        return "What barrel length?", "barrel"
    names = short_list([f"the {r.name}" for r in rifles])
    if names:
        return f"Which one -- {names}?", "name"
    return f"That's still {len(rifles)} rifles. Say a word from its name.", "name"


def narrow_loads(loads: list[Load], text: str) -> tuple[list[Load], bool]:
    """Loads that fit a spoken bullet weight ('the 140s', '140 grain')."""
    text = _spoken_numbers(text.lower())
    nums = [float(n) for n in _NUM_RE.findall(text)]
    if not nums:
        return list(loads), False
    fits = [ld for ld in loads if any(abs(ld.bullet_weight_gr - n) < 0.5 for n in nums)]
    return fits, True


def which_load_question(loads: list[Load], rifle_name: str) -> str:
    """'Which one -- A or B?' for a few loads; a bullet-weight question for many."""
    names = short_list([ld.name for ld in loads])
    if names:
        return f"Which one -- {names}?"
    weights = {ld.bullet_weight_gr for ld in loads}
    if len(weights) > 1:
        return f"You have {len(loads)} loads on the {rifle_name}. What bullet weight?"
    return f"You have {len(loads)} loads on the {rifle_name}. Say a word from its name."
