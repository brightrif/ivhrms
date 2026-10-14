"""Tells a Bahraini from everyone else, and a mistake from a nationality.

SIO is worked out differently for Bahrainis, so payroll must never guess: "Bahraini" and "Bahrain" are Bahraini; a
name on the known list is someone else; blank text, or text that is on neither list (a typing mistake such as
"Bahrani"), is `unknown`, and payroll stops until HR fixes the staff record."""
import re
import unicodedata

from .countries import COUNTRIES

BAHRAINI, OTHER, UNKNOWN = "bahraini", "other", "unknown"

LABELS = {BAHRAINI: "Bahraini", OTHER: "Not Bahraini", UNKNOWN: "Not recognised"}


def _norm(text):
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


_BAHRAINI = {_norm(t) for t in ("Bahraini", "Bahrain", "Bahraini national", "Bahraini citizen", "BH", "BHR",
                                "\u0628\u062d\u0631\u064a\u0646\u064a", "\u0628\u062d\u0631\u064a\u0646\u064a\u0629",
                                "\u0627\u0644\u0628\u062d\u0631\u064a\u0646", "\u0628\u062d\u0631\u064a\u0646")}
_KNOWN = {_norm(name) for line in COUNTRIES.strip().splitlines() for name in line.split("|")}


_GCC = {_norm(t) for t in ("Saudi", "Saudi Arabian", "Saudi Arabia", "Omani", "Oman", "Emirati", "UAE", "United Arab Emirates",
                           "Kuwaiti", "Kuwait", "Qatari", "Qatar")}


def is_gcc(text):
    """True for a GCC national other than Bahraini. They fall under the unified GCC insurance scheme, so the standard
    gratuity rules are not applied to them without a decision."""
    return _norm(text) in _GCC


def classify(text):
    """'bahraini', 'other' or 'unknown' for whatever is typed in the nationality box."""
    word = _norm(text)
    if not word:
        return UNKNOWN
    if word in _BAHRAINI:
        return BAHRAINI
    if word in _KNOWN:
        return OTHER
    return UNKNOWN
