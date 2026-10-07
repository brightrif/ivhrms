from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.compliance.testing import ComplianceCase
from apps.organization.models import CompanyAccess
from apps.vehicles import financing
from apps.vehicles.loans import LoanInstallment, VehicleLoan
from apps.vehicles.models import Vehicle


class LoanPageTests(ComplianceCase):
    """Loans are run by Finance, so these pages are tested as the Finance user."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.finance)
        self.v = Vehicle.objects.create(company=self.co, plate_number="123456", make="Toyota", model="Hilux",
                                        year=2022)

    def url(self, name, *args):
        return reverse(f"web:{name}", args=args)

    def iso(self, days=0):
        return (self.today + timedelta(days=days)).isoformat()

    def loan_data(self, **over):
        data = {"lender": "Bank of Bahrain", "account_no": "L-100", "financed_amount": "5000.000",
                "down_payment": "1000", "installment_count": "12", "installment_amount": "450.000",
                "final_installment_amount": "", "first_due_on": self.iso(20), "already_paid": "0", "notes": ""}
        data.update(over)
        return data

    def make_loan(self, v=None, **over):
        data = dict(lender="Bank of Bahrain", financed_amount=D("5000"), installment_count=12,
                    installment_amount=D("450"), first_due_on=self.today + timedelta(days=20))
        data.update(over)
        return financing.create_loan(v or self.v, **data)

    def test_adding_a_loan_builds_the_schedule(self):
        page = self.url("vehicle_loan", self.v.pk)
        self.assertContains(self.client.get(page), "No loan is recorded")
        add = self.url("vehicle_loan_add", self.v.pk)
        self.assertEqual(self.client.get(add).status_code, 200)
        self.assertRedirects(self.client.post(add, self.loan_data()), page)
        loan = VehicleLoan.objects.get()
        self.v.refresh_from_db()
        self.assertEqual((loan.installments.count(), self.v.ownership, loan.company), (12, "loan", self.co))
        r = self.client.get(page)
        for text in ("Bank of Bahrain", "450.000", "5400.000", "400.000"):          # lender, installment, total, finance cost
            self.assertContains(r, text)

    def test_an_old_loan_can_be_entered_with_its_first_installments_already_paid(self):
        first = (self.today - timedelta(days=95)).isoformat()
        self.client.post(self.url("vehicle_loan_add", self.v.pk), self.loan_data(first_due_on=first, already_paid="3"))
        self.assertEqual(LoanInstallment.objects.filter(status="paid").count(), 3)

    def test_bad_loans_are_form_errors_not_crashes(self):
        add = self.url("vehicle_loan_add", self.v.pk)
        r = self.client.post(add, self.loan_data(installment_count="10"))                  # repays 4,500 for 5,000
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "less than the")
        self.assertEqual(self.client.post(add, self.loan_data(lender="")).status_code, 200)
        self.assertEqual(self.client.post(add, self.loan_data(installment_count="500")).status_code, 200)
        self.assertEqual(self.client.post(add, self.loan_data(already_paid="1")).status_code, 200)   # not due yet
        self.assertFalse(VehicleLoan.objects.exists())

    def test_paying_and_undoing_an_installment(self):
        loan = self.make_loan()
        first = loan.installments.get(number=1)
        pay = self.url("vehicle_installment_pay", first.pk)
        page = self.url("vehicle_loan", self.v.pk)
        self.assertEqual(self.client.get(pay).status_code, 200)
        self.assertRedirects(self.client.post(pay, {"paid_on": self.iso(0), "amount": "450.000", "reference": "TT-1"}),
                             page)
        first.refresh_from_db()
        self.assertEqual((first.status, str(first.paid_amount)), ("paid", "450.000"))
        self.assertRedirects(self.client.get(pay), page)                                    # only once
        undo = self.url("vehicle_installment_undo", first.pk)
        self.assertEqual(self.client.post(undo, {"reason": ""}).status_code, 200)
        self.assertRedirects(self.client.post(undo, {"reason": "wrong installment"}), page)
        first.refresh_from_db()
        self.assertEqual(first.status, "due")

    def test_a_future_payment_date_is_a_form_error(self):
        first = self.make_loan().installments.get(number=1)
        r = self.client.post(self.url("vehicle_installment_pay", first.pk),
                             {"paid_on": self.iso(5), "amount": "450", "reference": ""})
        self.assertEqual(r.status_code, 200)
        first.refresh_from_db()
        self.assertEqual(first.status, "due")

    def test_early_settlement(self):
        loan = self.make_loan()
        settle = self.url("vehicle_loan_settle", loan.pk)
        r = self.client.get(settle)
        self.assertIn("5400.000", r.context["form"].fields["amount"].help_text)             # what is still due
        self.assertRedirects(
            self.client.post(settle, {"settled_on": self.iso(0), "amount": "4800", "reference": "SET-1"}),
            self.url("vehicle_loan", self.v.pk))
        loan.refresh_from_db()
        self.assertEqual((loan.status, loan.installments.filter(status="settled").count()), ("settled", 12))
        self.assertContains(self.client.get(self.url("vehicle_loan", self.v.pk)), "Settled early")
        self.assertRedirects(self.client.get(settle), self.url("vehicle_loan", self.v.pk))   # only once

    def test_cancelling_a_loan_entered_by_mistake(self):
        loan = self.make_loan()
        void = self.url("vehicle_loan_void", loan.pk)
        self.assertEqual(self.client.post(void, {"reason": ""}).status_code, 200)
        self.assertRedirects(self.client.post(void, {"reason": "wrong vehicle"}), self.url("vehicle_loan", self.v.pk))
        loan.refresh_from_db()
        self.assertTrue(loan.is_voided)
        self.assertContains(self.client.get(self.url("vehicle_loan", self.v.pk)), "No loan is recorded")

    def test_the_fleet_overview(self):
        self.make_loan()
        self.make_loan(Vehicle.objects.create(company=self.co, plate_number="777", make="Kia"),
                       installment_amount=D("500"), first_due_on=self.today - timedelta(days=5))
        r = self.client.get(self.url("vehicle_loans"))
        self.assertContains(r, "123456")
        self.assertContains(r, "777")
        self.assertContains(r, "950.000")                                                   # monthly installments
        self.assertContains(r, "Overdue")

    def test_access_by_role_and_company(self):
        loan = self.make_loan()
        inst = loan.installments.get(number=1)
        page, overview = self.url("vehicle_loan", self.v.pk), self.url("vehicle_loans")
        self.client.logout()
        self.assertEqual(self.client.get(page).status_code, 302)                            # sign in first
        for user in (self.nobody, self.hr):                                                 # HR does not see loans
            self.client.force_login(user)
            for url in (page, overview, self.url("vehicle_loan_add", self.v.pk),
                        self.url("vehicle_installment_pay", inst.pk)):
                self.assertEqual(self.client.get(url).status_code, 403, (user.username, url))
        self.client.force_login(self.boss)                                                  # Management only looks
        self.assertEqual(self.client.get(page).status_code, 200)
        self.assertEqual(self.client.get(overview).status_code, 200)
        for url in (self.url("vehicle_loan_add", self.v.pk), self.url("vehicle_installment_pay", inst.pk),
                    self.url("vehicle_loan_settle", loan.pk)):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        other = get_user_model().objects.create_user("fin2", email="fin2@example.com")      # Finance in another company
        other.groups.add(Group.objects.get(name="Finance"))
        CompanyAccess.objects.create(user=other, company=self.other_co)
        self.client.force_login(other)
        for url in (page, self.url("vehicle_installment_pay", inst.pk), self.url("vehicle_loan_settle", loan.pk)):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertNotContains(self.client.get(overview), "123456")

    def test_every_form_places_every_field_it_has(self):
        loan = self.make_loan()
        inst = loan.installments.get(number=1)
        self.v2 = Vehicle.objects.create(company=self.co, plate_number="888", make="Kia")
        pages = [self.url("vehicle_loan_add", self.v2.pk), self.url("vehicle_installment_pay", inst.pk),
                 self.url("vehicle_installment_undo", inst.pk), self.url("vehicle_loan_settle", loan.pk),
                 self.url("vehicle_loan_void", loan.pk)]
        for url in pages:
            r = self.client.get(url)
            placed = {n for section in r.context["layout"] for row in section["rows"] for n in row}
            self.assertEqual(placed, set(r.context["form"].fields), url)


class LoanOnVehiclePageTests(ComplianceCase):
    """The vehicle page and the sell page, which now know about loans."""

    def setUp(self):
        super().setUp()
        self.v = Vehicle.objects.create(company=self.co, plate_number="123456", make="Toyota", model="Hilux",
                                        year=2022)
        financing.create_loan(self.v, lender="Bank of Bahrain", financed_amount=D("5000"), installment_count=12,
                              installment_amount=D("450"), first_due_on=self.today + timedelta(days=20))

    def test_loan_figures_are_for_finance_and_management_only(self):
        page = reverse("web:vehicle_detail", args=[self.v.pk])
        for user in (self.finance, self.boss):
            self.client.force_login(user)
            self.assertContains(self.client.get(page), "Bank of Bahrain")
        self.client.force_login(self.hr)
        r = self.client.get(page)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Bank of Bahrain")
        self.assertNotContains(r, "Bank loan")

    def test_a_financed_vehicle_cannot_be_sold_until_the_loan_is_settled(self):
        self.client.force_login(self.hr)
        sell = reverse("web:vehicle_sell", args=[self.v.pk])
        r = self.client.post(sell, {"sold_on": self.today.isoformat()})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "active bank loan")
        self.v.refresh_from_db()
        self.assertEqual(self.v.status, "active")
