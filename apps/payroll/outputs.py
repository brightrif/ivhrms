"""What leaves the screen: the payroll register, the bank transfer sheet and the cash pay list, as Excel files.
They use the same small writer as the labor reports and read the frozen lines of the run."""
from decimal import Decimal

from apps.labor import xlsx
from apps.labor.xlsx import Cell, Sheet

ZERO = Decimal("0")


def _title(run, what):
    return [[Cell(f"{what}: {run.label}", xlsx.TITLE)],
            [Cell(f"{run.company.name}. Status: {run.get_status_display()}", xlsx.NOTE)], []]


def _sum(lines, name):
    return sum((getattr(line, name) for line in lines), ZERO)


def _eosg(line):
    return Decimal((line.breakdown.get("eosg") or {}).get("amount", "0"))


def register(run):
    lines = list(run.lines.all())
    head = ["No.", "Name", "Days", "Gross", "Absence", "SIO (employee)", "Additions", "Deductions", "Net pay",
            "SIO (employer)", "Paid by", "Paid on", "SIO gratuity (company)"]
    rows = _title(run, "Payroll register") + [[Cell(h, xlsx.HEADER) for h in head]]
    for line in lines:
        rows.append([line.employee_no, line.employee_name, Cell(line.days_employed, xlsx.INT),
                     Cell(line.gross, xlsx.MONEY), Cell(line.absence_deduction, xlsx.MONEY),
                     Cell(line.sio_employee, xlsx.MONEY), Cell(line.additions, xlsx.MONEY),
                     Cell(line.deductions, xlsx.MONEY), Cell(line.net, xlsx.MONEY),
                     Cell(line.sio_employer, xlsx.MONEY), line.get_payment_label(), line.paid_on,
                     Cell(_eosg(line), xlsx.MONEY)])
    rows.append([Cell("Total", xlsx.TEXT_BOLD), "", "", Cell(_sum(lines, "gross"), xlsx.MONEY_BOLD),
                 Cell(_sum(lines, "absence_deduction"), xlsx.MONEY_BOLD), Cell(_sum(lines, "sio_employee"), xlsx.MONEY_BOLD),
                 Cell(_sum(lines, "additions"), xlsx.MONEY_BOLD), Cell(_sum(lines, "deductions"), xlsx.MONEY_BOLD),
                 Cell(_sum(lines, "net"), xlsx.MONEY_BOLD), Cell(_sum(lines, "sio_employer"), xlsx.MONEY_BOLD)])
    return xlsx.build_workbook([Sheet("Register", rows, widths=[10, 28, 7, 12, 12, 14, 12, 12, 12, 14, 14, 12, 16], freeze_row=4)])


def bank(run):
    lines = [line for line in run.lines.all() if line.payment_method == "bank"]
    head = ["No.", "Name", "Bank", "IBAN", "Net pay (BHD)"]
    rows = _title(run, "Bank transfer sheet") + [[Cell(h, xlsx.HEADER) for h in head]]
    for line in lines:
        rows.append([line.employee_no, line.employee_name, line.bank_name, line.iban, Cell(line.net, xlsx.MONEY)])
    rows.append([Cell("Total", xlsx.TEXT_BOLD), "", "", "", Cell(_sum(lines, "net"), xlsx.MONEY_BOLD)])
    return xlsx.build_workbook([Sheet("Bank", rows, widths=[10, 28, 22, 28, 14], freeze_row=4)])


def cash(run):
    lines = [line for line in run.lines.all() if line.payment_method == "cash"]
    head = ["No.", "Name", "Net pay (BHD)", "Signature", "Date received"]
    rows = _title(run, "Cash pay list") + [[Cell(h, xlsx.HEADER) for h in head]]
    for line in lines:
        rows.append([line.employee_no, line.employee_name, Cell(line.net, xlsx.MONEY), "", line.paid_on])
    rows.append([Cell("Total", xlsx.TEXT_BOLD), "", Cell(_sum(lines, "net"), xlsx.MONEY_BOLD)])
    return xlsx.build_workbook([Sheet("Cash", rows, widths=[10, 28, 14, 28, 14], freeze_row=4)])


BUILDERS = {"register": register, "bank": bank, "cash": cash}
