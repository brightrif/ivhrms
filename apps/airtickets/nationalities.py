"""Nationalities as people write them.

The employee record keeps whatever was typed. This list turns it into ONE spelling, so "india", "India" and "Indian"
are the same person for the ticket rules, and it feeds the suggestions on the forms. A nationality that is not in the
list is not an error: it is kept as typed and shown on the Air Tickets page as "not recognised" so someone can fix it
or add it here."""
import re

# (what is stored, the country, other spellings people use)
NATIONALITIES = [
    ("Bahraini", "Bahrain", ["bahrani"]),
    ("Saudi", "Saudi Arabia", ["saudi arabian", "ksa"]),
    ("Kuwaiti", "Kuwait", []),
    ("Omani", "Oman", []),
    ("Qatari", "Qatar", []),
    ("Emirati", "United Arab Emirates", ["uae", "emarati", "emirates", "uae national"]),
    ("Indian", "India", []),
    ("Pakistani", "Pakistan", []),
    ("Bangladeshi", "Bangladesh", ["bangladesi"]),
    ("Sri Lankan", "Sri Lanka", ["srilankan", "ceylonese"]),
    ("Nepali", "Nepal", ["nepalese"]),
    ("Filipino", "Philippines", ["philippino", "phillipino", "philipino", "pilipino", "filipina", "philippines national"]),
    ("Indonesian", "Indonesia", []),
    ("Thai", "Thailand", []),
    ("Vietnamese", "Vietnam", []),
    ("Malaysian", "Malaysia", []),
    ("Singaporean", "Singapore", []),
    ("Chinese", "China", []),
    ("Japanese", "Japan", []),
    ("Korean", "South Korea", ["south korean", "korea"]),
    ("Afghan", "Afghanistan", ["afghani"]),
    ("Maldivian", "Maldives", []),
    ("Egyptian", "Egypt", []),
    ("Jordanian", "Jordan", []),
    ("Lebanese", "Lebanon", []),
    ("Syrian", "Syria", []),
    ("Iraqi", "Iraq", []),
    ("Iranian", "Iran", ["persian"]),
    ("Yemeni", "Yemen", []),
    ("Palestinian", "Palestine", []),
    ("Sudanese", "Sudan", []),
    ("Moroccan", "Morocco", []),
    ("Tunisian", "Tunisia", []),
    ("Algerian", "Algeria", []),
    ("Libyan", "Libya", []),
    ("Ethiopian", "Ethiopia", []),
    ("Eritrean", "Eritrea", []),
    ("Kenyan", "Kenya", []),
    ("Ugandan", "Uganda", []),
    ("Tanzanian", "Tanzania", []),
    ("Ghanaian", "Ghana", []),
    ("Nigerian", "Nigeria", []),
    ("Cameroonian", "Cameroon", []),
    ("Zimbabwean", "Zimbabwe", []),
    ("South African", "South Africa", []),
    ("Mauritian", "Mauritius", []),
    ("British", "United Kingdom", ["uk", "english", "scottish", "welsh", "great britain", "gb"]),
    ("Irish", "Ireland", []),
    ("American", "United States", ["usa", "us", "united states of america", "us citizen"]),
    ("Canadian", "Canada", []),
    ("Australian", "Australia", []),
    ("New Zealander", "New Zealand", ["nz", "kiwi"]),
    ("French", "France", []),
    ("German", "Germany", []),
    ("Italian", "Italy", []),
    ("Spanish", "Spain", []),
    ("Portuguese", "Portugal", []),
    ("Dutch", "Netherlands", ["holland"]),
    ("Belgian", "Belgium", []),
    ("Swedish", "Sweden", []),
    ("Norwegian", "Norway", []),
    ("Danish", "Denmark", []),
    ("Polish", "Poland", []),
    ("Romanian", "Romania", []),
    ("Bulgarian", "Bulgaria", []),
    ("Ukrainian", "Ukraine", []),
    ("Russian", "Russia", []),
    ("Turkish", "Turkey", ["turkiye"]),
    ("Greek", "Greece", []),
    ("Serbian", "Serbia", []),
    ("Georgian", "Georgia", []),
    ("Armenian", "Armenia", []),
    ("Kazakh", "Kazakhstan", []),
    ("Uzbek", "Uzbekistan", []),
    ("Brazilian", "Brazil", []),
    ("Argentine", "Argentina", ["argentinian"]),
    ("Mexican", "Mexico", []),
    ("Colombian", "Colombia", []),
    ("Peruvian", "Peru", []),
]


def _clean(text):
    """Lower case, no punctuation, single spaces: ' U.S.A ' -> 'usa', 'Sri-Lankan' -> 'sri lankan'."""
    text = re.sub(r"[-_/]", " ", text or "")
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split()).casefold()


_INDEX = {}
for _name, _country, _aliases in NATIONALITIES:
    for _spelling in (_name, _country, *_aliases):
        _INDEX.setdefault(_clean(_spelling), _name)


def canonical(text):
    """The one spelling for what was typed ('india' -> 'Indian'), or None when it is empty or not in the list."""
    return _INDEX.get(_clean(text))


def key(text):
    """What two nationalities are compared on: the canonical spelling when known, else the cleaned text."""
    return _clean(canonical(text) or text)


def known_names():
    return sorted({name for name, _, _ in NATIONALITIES})
