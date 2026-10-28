"""Put reference and recognized ATC text into one canonical token form so they can be compared.

References in the open corpora are spoken words ("csa one delta zulu descend flight level one
hundred"). A recognizer may write "CSA1DZ descend FL100". Both become
["csa", "1", "d", "z", "descend", "flight", "level", "1", "0", "0"]: every spelled letter is a
single lowercase letter, every digit a single digit, and other words stay as words.
"""

from __future__ import annotations

import re

ICAO_LETTERS = {
    "alfa": "a", "alpha": "a", "bravo": "b", "charlie": "c", "delta": "d", "echo": "e",
    "foxtrot": "f", "golf": "g", "hotel": "h", "india": "i", "juliett": "j", "juliet": "j",
    "kilo": "k", "lima": "l", "mike": "m", "november": "n", "oscar": "o", "papa": "p",
    "quebec": "q", "romeo": "r", "sierra": "s", "tango": "t", "uniform": "u", "victor": "v",
    "whiskey": "w", "whisky": "w", "xray": "x", "yankee": "y", "zulu": "z",
}  # fmt: skip
LETTER_WORDS = {
    "a": "alfa", "b": "bravo", "c": "charlie", "d": "delta", "e": "echo", "f": "foxtrot",
    "g": "golf", "h": "hotel", "i": "india", "j": "juliett", "k": "kilo", "l": "lima",
    "m": "mike", "n": "november", "o": "oscar", "p": "papa", "q": "quebec", "r": "romeo",
    "s": "sierra", "t": "tango", "u": "uniform", "v": "victor", "w": "whiskey", "x": "xray",
    "y": "yankee", "z": "zulu",
}  # fmt: skip
DIGITS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "niner": "9", "nineer": "9", "tree": "3", "fife": "5",
}  # fmt: skip
DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six",
    "7": "seven", "8": "eight", "9": "nine",
}  # fmt: skip
TEENS = {
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19",
}  # fmt: skip
TENS = {
    "twenty": "2", "thirty": "3", "forty": "4", "fifty": "5", "sixty": "6", "seventy": "7",
    "eighty": "8", "ninety": "9",
}  # fmt: skip
SYNONYMS = {"point": "decimal", "okay": "ok", "x-ray": "xray", "fl": "flight level"}
COMPOUNDS = {("aero", "flot"): "aeroflot", ("sky", "travel"): "skytravel"}
# Upper-case words a recognizer may write that are spoken as words, not spelled out.
ACRONYMS = {"ILS", "QNH", "QFE", "VOR", "DME", "ATIS", "RNAV", "NDB", "TCAS", "CAVOK", "VFR", "IFR"}

_PARENS = re.compile(r"\([^)]*\)?|\S*\)")
_DECIMAL = re.compile(r"(?<=\d)[.,](?=\d)")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
_ALNUM_RUNS = re.compile(r"[A-Za-z]+|\d+")


def _split_token(token: str) -> list[str]:
    """Split one raw token (original case) into canonical pieces."""
    lower = token.lower()
    if lower in SYNONYMS:
        return SYNONYMS[lower].split()
    if token.isdigit():
        return list(token)
    if token.isalpha():
        if token.isupper() and len(token) >= 4 and token not in ACRONYMS:
            return list(lower)  # a registration written as letters, e.g. OKFAO
        return [lower]
    pieces: list[str] = []
    for i, run in enumerate(_ALNUM_RUNS.findall(token)):
        if run.isdigit():
            pieces.extend(run)
        elif run.lower() == "fl":
            pieces.extend(["flight", "level"])
        elif i == 0 and len(run) >= 3:
            pieces.append(run.lower())  # airline designator, e.g. CSA in CSA1DZ
        else:
            pieces.extend(run.lower())
    return pieces


def _below_thousand(words: list[str], j: int) -> tuple[list[str], int]:
    """Digits for what follows "thousand": "four thousand five hundred" is 4500, not 4000500.
    Returns the three digits and how many words they used."""
    value, used = 0, 0
    if j + 1 < len(words) and words[j] in DIGITS and words[j + 1] == "hundred":
        value, used = 100 * int(DIGITS[words[j]]), 2
    w = words[j + used] if j + used < len(words) else None
    if w in TEENS:
        value, used = value + int(TEENS[w]), used + 1
    elif w in TENS:
        value, used = value + 10 * int(TENS[w]), used + 1
        unit = words[j + used] if j + used < len(words) else None
        if unit in DIGITS and DIGITS[unit] != "0":
            value, used = value + int(DIGITS[unit]), used + 1
    return list(f"{value:03d}"), used


def _numbers(words: list[str]) -> list[str]:
    """Turn spoken number and letter words into single digits and letters."""
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else None
        if w in ("double", "triple") and nxt is not None:
            repeat = 2 if w == "double" else 3
            unit = _numbers([nxt])
            if len(unit) == 1:
                out.extend(unit * repeat)
                i += 2
                continue
        if w in DIGITS:
            out.append(DIGITS[w])
        elif w in ICAO_LETTERS:
            out.append(ICAO_LETTERS[w])
        elif w in TEENS:
            out.extend(TEENS[w])
        elif w in TENS:
            if nxt in DIGITS and DIGITS[nxt] != "0":
                out.extend([TENS[w], DIGITS[nxt]])
                i += 1
            else:
                out.extend([TENS[w], "0"])
        elif w == "hundred" and out and out[-1].isdigit():
            out.extend(["0", "0"])
        elif w == "thousand" and out and out[-1].isdigit():
            rest, used = _below_thousand(words, i + 1)
            out.extend(rest)
            i += used
        else:
            out.append(w)
        i += 1
    return out


def canonical(text: str) -> list[str]:
    text = _PARENS.sub(" ", text)
    text = _THOUSANDS.sub("", text)
    text = _DECIMAL.sub(" decimal ", text)
    text = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", "", text)  # OK-FAO -> OKFAO
    text = re.sub(r"[^\w\s]", " ", text)
    words: list[str] = []
    for token in text.split():
        words.extend(_split_token(token))
    merged: list[str] = []
    for w in words:
        if merged and (merged[-1], w) in COMPOUNDS:
            merged[-1] = COMPOUNDS[(merged[-1], w)]
        else:
            merged.append(w)
    return _numbers(merged)


def spoken(tokens: list[str]) -> str:
    """Render canonical tokens back into spoken ATC words, e.g. for keyword lists."""
    return " ".join(LETTER_WORDS.get(t, DIGIT_WORDS.get(t, t)) for t in tokens)
