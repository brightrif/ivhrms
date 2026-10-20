"""The Loans section of the Payroll tab in Settings: the default interest and three optional limits.

Every limit is empty (0) to start, which means no limit. When one is filled in and a loan goes over it, HR gets a clear
warning and must give a reason to go ahead; the reason is kept on the loan."""
from decimal import Decimal

KEYS = {"interest": "payroll.loan_interest", "salaries": "payroll.loan_max_salaries",
        "months": "payroll.loan_max_months", "share": "payroll.loan_max_share"}


def loan_settings():
    from apps.configuration.registry import Setting

    return [
        Setting(KEYS["interest"], "Yearly interest on loans, %", "decimal", Decimal("0"), per_company=True,
                minimum=Decimal("0"), maximum=Decimal("100"),
                help="0 means no interest. Flat, on the amount, for the whole term. HR can change it on one loan."),
        Setting(KEYS["salaries"], "Largest loan, in monthly salaries", "decimal", Decimal("0"), per_company=True,
                minimum=Decimal("0"), help="0 means no limit."),
        Setting(KEYS["months"], "Most instalments", "int", 0, per_company=True, minimum=0, maximum=120,
                help="0 means no limit."),
        Setting(KEYS["share"], "Largest share of a month's pay that instalments may take, %", "int", 0,
                per_company=True, minimum=0, maximum=100, help="0 means no limit."),
    ]


def _get(key, company):
    from apps.configuration import services as settings_service

    try:
        return settings_service.get(key, company)
    except KeyError:                                   # the Settings section is not installed: no limits
        return None


def default_interest(company):
    value = _get(KEYS["interest"], company)
    return Decimal(str(value)) if value else Decimal("0")


def limits_for(company):
    """{'salaries': Decimal|None, 'months': int|None, 'share': int|None}: None means no limit."""
    out = {}
    for name in ("salaries", "months", "share"):
        value = _get(KEYS[name], company)
        out[name] = (Decimal(str(value)) if name == "salaries" else int(value)) if value else None
    return out
