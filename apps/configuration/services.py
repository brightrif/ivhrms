"""Reading and changing settings. Everything else asks here; nothing reads the table directly."""
from decimal import Decimal, InvalidOperation

from django.db import transaction

from . import registry
from .models import SettingValue


class SettingError(Exception):
    """A value was refused. The message is written to be shown to the person."""


def _definition(key):
    setting = registry.find_setting(key)
    if setting is None:
        raise KeyError(f"No setting is registered as {key!r}")
    return setting


def _decode(setting, stored):
    if setting.kind == "decimal":
        return Decimal(str(stored))
    return stored


def _encode(setting, value):
    return str(value) if setting.kind == "decimal" else value


def resolve(key, company=None):
    """(value, source) where source says where it came from: "company", "default" or "built-in"."""
    setting = _definition(key)
    rows = {r.company_id: r for r in SettingValue.objects.filter(key=key)}
    if company is not None and setting.per_company:
        row = rows.get(getattr(company, "pk", company))
        if row is not None:
            return _decode(setting, row.value), "company"
    row = rows.get(None)
    if row is not None:
        return _decode(setting, row.value), "default"
    return setting.default, "built-in"


def get(key, company=None):
    """The value in force for a company (or for everyone, when no company is given)."""
    return resolve(key, company)[0]


def clean(setting, raw):
    """Turn what was typed into the right type, or raise SettingError saying what is wrong."""
    label = setting.label
    if setting.kind == "bool":
        if isinstance(raw, bool):
            return raw
        if str(raw).lower() in ("1", "true", "on", "yes"):
            return True
        if str(raw).lower() in ("0", "false", "off", "no", ""):
            return False
        raise SettingError(f"{label}: choose yes or no.")
    if setting.kind == "choice":
        allowed = {str(v) for v, _ in setting.choices}
        if str(raw) not in allowed:
            raise SettingError(f"{label}: choose one of the listed options.")
        return str(raw)
    try:
        number = Decimal(str(raw).strip())
    except InvalidOperation:
        raise SettingError(f"{label}: enter a number.") from None
    if not number.is_finite():
        raise SettingError(f"{label}: enter a number.")
    if setting.kind == "int":
        if number != number.to_integral_value():
            raise SettingError(f"{label}: enter a whole number.")
        number = int(number)
    if setting.minimum is not None and number < setting.minimum:
        raise SettingError(f"{label}: must be at least {setting.minimum}.")
    if setting.maximum is not None and number > setting.maximum:
        raise SettingError(f"{label}: must be at most {setting.maximum}.")
    return number


@transaction.atomic
def set_value(key, raw, company=None):
    """Choose a value. A company override is only allowed for per-company settings; otherwise it is the default for all.
    Returns the stored SettingValue. The audit log records who changed what."""
    setting = _definition(key)
    value = clean(setting, raw)
    scope = company if (company is not None and setting.per_company) else None
    if company is not None and not setting.per_company:
        raise SettingError(f"{setting.label} applies to all companies and cannot be set for one.")
    row, _ = SettingValue.objects.get_or_create(key=key, company=scope, defaults={"value": _encode(setting, value)})
    if _decode(setting, row.value) != value:
        row.value = _encode(setting, value)
        row.save()
    return row


@transaction.atomic
def use_default(key, company):
    """Drop a company's override so it follows the default again. Returns True if there was one."""
    deleted, _ = SettingValue.objects.filter(key=key, company=company).delete()
    return bool(deleted)
