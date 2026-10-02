from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from admissions import tests as fixtures
from progression.models import Payment
from .models import CommissionRule, Invoice
from .services import commission_amount,allocate_payment


class FinanceFoundationTests(TestCase):
    setUp=fixtures.AdmissionTests.setUp
    user=fixtures.AdmissionTests.user

    def test_commission_rounding_and_currency_boundaries(self):
        rule=CommissionRule.objects.create(institution=self.uni,name='Tuition commission',version=1,method='PERCENTAGE',value='12.50',currency='USD',created_by=self.admin)
        self.assertEqual(commission_amount(rule,'1000.04','USD'),Decimal('125.01'))
        with self.assertRaises(ValidationError):commission_amount(rule,100,'NPR')
        with self.assertRaises(ValidationError):commission_amount(rule,-1,'USD')
        rule.method='FIXED';rule.value=Decimal('50')
        self.assertEqual(commission_amount(rule,1000,'USD'),Decimal('50.00'))

    def test_invoice_allocation_cannot_overspend_or_cross_currency(self):
        invoice=Invoice.objects.create(reference='INV-TEST',person=self.person,currency='USD',description='Consultancy fee',amount=100,status='ISSUED',created_by=self.admin)
        payment=Payment.objects.create(ref='PAY-TEST',person=self.person,amount=150,currency='USD',paid_on=timezone.localdate(),method='BANK_TRANSFER',status='VERIFIED',created_by=self.admin)
        with self.assertRaises(ValidationError):allocate_payment(invoice.pk,payment.pk,101,self.admin)
        allocation=allocate_payment(invoice.pk,payment.pk,100,self.admin)
        self.assertEqual(allocation.amount,Decimal('100'))
        with self.assertRaises(ValidationError):allocate_payment(invoice.pk,payment.pk,1,self.admin)
        invoice=Invoice.objects.create(reference='INV-OTHER',person=self.person,currency='NPR',description='Other fee',amount=100,status='ISSUED',created_by=self.admin)
        with self.assertRaises(ValidationError):allocate_payment(invoice.pk,payment.pk,10,self.admin)
        invoice.currency='USD';invoice.save()
        with self.assertRaises(ValidationError):allocate_payment(invoice.pk,payment.pk,51,self.admin)
        payment.status='REFUNDED';payment.save()
        with self.assertRaises(ValidationError):allocate_payment(invoice.pk,payment.pk,10,self.admin)
