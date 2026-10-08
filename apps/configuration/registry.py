"""What can be set, and who can be given the right to do what.

A module describes itself once, in its own settings_spec.py, by registering a Module. The Settings page and the
Access grid are drawn from this list, so adding a module's tab needs no change here and no new page code."""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Optional

KINDS = ("bool", "int", "decimal", "choice")


@dataclass(frozen=True)
class Setting:
    """One thing a person can choose. `key` is "module.name". `per_company` means each company may override the
    default for all companies. Settings that change money or history do NOT belong here: they stay in their own dated
    tables (like the overtime rules) and appear on the page as a custom section."""
    key: str
    label: str
    kind: str
    default: object
    help: str = ""
    choices: tuple = ()                    # for kind "choice": ((value, label), ...)
    minimum: Optional[Decimal] = None
    maximum: Optional[Decimal] = None
    per_company: bool = False

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"{self.key}: unknown kind {self.kind!r}")
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"{self.key}: a choice setting needs choices")
        if "." not in self.key:
            raise ValueError(f"{self.key}: keys look like 'module.name'")


@dataclass
class Section:
    """A block on a tab: either plain settings, or a custom template with its own context (for dated policies)."""
    title: str
    settings: list = field(default_factory=list)
    intro: str = ""
    template: Optional[str] = None
    context: Optional[Callable] = None     # (user) -> dict, only for custom sections


@dataclass(frozen=True)
class Capability:
    """A right that can be given to a group: an existing Django permission with a plain-words label."""
    label: str
    perm: str                              # "app_label.codename"
    hint: str = ""


@dataclass
class Module:
    key: str
    label: str
    order: int = 100
    icon: str = "bi-gear"
    sections: list = field(default_factory=list)
    capabilities: list = field(default_factory=list)

    @property
    def settings(self):
        return [s for sec in self.sections for s in sec.settings]

    @property
    def view_perm(self):
        return f"configuration.view_settings_{self.key}"

    @property
    def edit_perm(self):
        return f"configuration.edit_settings_{self.key}"


_MODULES = {}


def register(module):
    """Add (or replace) a module. Replacing keeps reloads and tests simple; a duplicate setting key is an error."""
    taken = {s.key: m.key for m in _MODULES.values() if m.key != module.key for s in m.settings}
    for s in module.settings:
        if s.key in taken:
            raise ValueError(f"{s.key} is already registered by {taken[s.key]}")
        if not s.key.startswith(module.key + "."):
            raise ValueError(f"{s.key} must start with '{module.key}.'")
    _MODULES[module.key] = module
    return module


def unregister(key):
    _MODULES.pop(key, None)


def modules():
    return sorted(_MODULES.values(), key=lambda m: (m.order, m.label))


def get_module(key):
    return _MODULES.get(key)


def find_setting(key):
    for m in _MODULES.values():
        for s in m.settings:
            if s.key == key:
                return s
    return None
