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
        invoice=Invoice.objects.create(reference='INV-BALANCE',person=self.person,currency='USD',description='Balance charge',amount=100,status='ISSUED',created_by=self.admin)
        with self.assertRaises(ValidationError):allocate_payment(invoice.pk,payment.pk,51,self.admin)
        payment.status='REFUNDED';payment.save()
        with self.assertRaises(ValidationError):allocate_payment(invoice.pk,payment.pk,10,self.admin)


class FinanceApiTests(TestCase):
    setUp=fixtures.AdmissionTests.setUp
    user=fixtures.AdmissionTests.user
    create=fixtures.AdmissionTests.create

    def post(self,action,**data):return self.client.post('/api/v1/finance/',{'action':action,**data},format='json')
    def invoice(self,currency='USD'):
        self.client.force_authenticate(self.admin)
        r=self.post('invoice_create',person_id=str(self.person.pk),description='Consultancy charge',amount=100,currency=currency)
        self.assertEqual(r.status_code,201,r.data);return r.data['record']
    def payment(self,**extra):
        self.client.force_authenticate(self.admin)
        r=self.post('payment_create',person_id=str(self.person.pk),amount=100,currency='USD',paid_on=str(timezone.localdate()),method='BANK_TRANSFER',**extra)
        self.assertEqual(r.status_code,201,r.data);return r.data['record']
    def upload(self,kind,pk):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        image=io.BytesIO();Image.new('RGB',(20,20),'white').save(image,format='PNG')
        r=self.client.post(f'/api/v1/finance/{kind}/{pk}/proof/',{'file':SimpleUploadedFile('proof.png',image.getvalue())},format='multipart')
        self.assertEqual(r.status_code,201,r.data);return r.data
    def verify(self,payment):
        for state in ['RECEIVED','VERIFIED']:
            r=self.post('payment_status',id=payment['id'],status=state,reason='Bank evidence checked');self.assertEqual(r.status_code,200,r.data)

    def test_roles_branch_scope_and_management_read_only(self):
        from crm.models import Branch,Person
        invoice=self.invoice();manager=self.user('manager','MANAGER');finance=self.user('finance','FINANCE');management=self.user('management','MANAGEMENT')
        for actor,expected in [(self.admin,200),(manager,200),(finance,200),(management,200),(self.owner,403),(self.docs,403)]:
            self.client.force_authenticate(actor);self.assertEqual(self.client.get('/api/v1/finance/').status_code,expected)
        self.client.force_authenticate(manager);self.assertEqual(self.post('rule_create',institution_id=self.uni.pk,name='Unauthorized global rule',method='FIXED',value=50,currency='USD').status_code,403)
        self.client.force_authenticate(management);self.assertEqual(self.post('invoice_status',id=invoice['id'],status='ISSUED',reason='Unauthorized').status_code,403)
        branch=Branch.objects.create(name='Other finance branch',code='FIN2');other=Person.objects.create(ref='FIN-OTHER',full_name='Other branch student',branch=branch,owner=self.other,source=self.person.source)
        hidden=Invoice.objects.create(reference='INV-HIDDEN',person=other,currency='USD',description='Private fee',amount=10,created_by=self.admin)
        self.client.force_authenticate(finance);self.assertEqual(self.client.get('/api/v1/finance/').data['count'],1)
        self.assertEqual(self.client.get(f'/api/v1/finance/invoices/{hidden.pk}/pdf/').status_code,404)
        self.assertEqual(self.post('invoice_create',person_id=str(other.pk),description='Hidden',amount=10,currency='USD').status_code,404)

    def test_invoice_allocation_refund_receipt_and_expense_review(self):
        from .models import Receipt
        invoice=self.invoice();payment=self.payment()
        self.assertEqual(self.post('invoice_status',id=invoice['id'],status='ISSUED',reason='Charge agreed').status_code,200)
        self.assertEqual(self.post('payment_status',id=payment['id'],status='VERIFIED',reason='Skip controls').status_code,400)
        self.upload('payments',payment['id']);self.verify(payment)
        self.assertEqual(self.post('allocate',invoice_id=invoice['id'],payment_id=payment['id'],amount=101,reason='Excess').status_code,400)
        r=self.post('allocate',invoice_id=invoice['id'],payment_id=payment['id'],amount=100,reason='Settle charge');self.assertEqual(r.status_code,200,r.data);self.assertEqual(Decimal(r.data['record']['balance']),Decimal(0))
        self.assertEqual(self.post('invoice_edit',id=invoice['id'],description='Changed',amount=50,currency='USD',reason='Edit issued').status_code,400)
        self.assertEqual(self.post('invoice_status',id=invoice['id'],status='CANCELLED',reason='Cancel paid').status_code,400)
        receipt=Receipt.objects.get(payment_id=payment['id']);pdf=self.client.get(f'/api/v1/finance/receipts/{receipt.pk}/pdf/');self.assertEqual(pdf.status_code,200);self.assertTrue(pdf.content.startswith(b'%PDF-'))
        self.assertEqual(self.post('payment_status',id=payment['id'],status='REFUNDED',reason='Refund approved').status_code,200)
        self.assertEqual(Decimal(self.client.get('/api/v1/finance/?kind=invoices').data['results'][0]['balance']),Decimal(100))
        import pymupdf
        with pymupdf.open(stream=self.client.get(f'/api/v1/finance/receipts/{receipt.pk}/pdf/').content,filetype='pdf') as doc:self.assertIn('REFUNDED',doc[0].get_text())
        self.assertEqual(Receipt.objects.count(),1)
        self.assertEqual(self.post('invoice_status',id=invoice['id'],status='CANCELLED',reason='Charge withdrawn').status_code,200)
        r=self.post('expense_create',branch_id=self.branch.pk,category='Office rent',payee='Landlord',amount=500,currency='NPR',incurred_on=str(timezone.localdate()));self.assertEqual(r.status_code,201,r.data);pk=r.data['record']['id']
        self.assertEqual(self.post('expense_status',id=pk,status='VERIFIED',reason='Review').status_code,400)
        item=self.upload('expenses',pk)
        self.assertEqual(self.post('expense_status',id=pk,status='VERIFIED',reason='Receipt reviewed').status_code,200)
        self.assertEqual(self.client.get(f"/api/v1/finance/files/{item['id']}/").status_code,200)
        self.assertEqual(self.post('expense_status',id=pk,status='VOIDED',reason='Duplicate corrected').status_code,200)

    def test_commission_snapshot_partial_receipts_and_overreceipt(self):
        from .models import InstitutionCommission
        self.client.force_authenticate(self.admin);app=self.create()
        r=self.post('rule_create',institution_id=self.uni.pk,name='Tuition commission',method='PERCENTAGE',value=10,currency='USD');self.assertEqual(r.status_code,201,r.data);rule=r.data['record']
        r=self.post('commission_create',application_id=str(app.pk),rule_id=rule['id'],base_amount=1000,currency='USD',reason='Institution contract');self.assertEqual(r.status_code,201,r.data);commission=r.data['record']
        self.assertEqual(Decimal(commission['expected_amount']),Decimal('100.00'))
        self.assertEqual(self.post('rule_create',institution_id=self.uni.pk,name='Tuition commission',method='PERCENTAGE',value=20,currency='USD').status_code,201)
        self.assertEqual(Decimal(InstitutionCommission.objects.get(pk=commission['id']).rule_snapshot['value']),Decimal(10))
        r=self.post('commission_receive',id=commission['id'],amount=40,received_on=str(timezone.localdate()),transaction_reference='INST-TX1',reason='Bank transfer');self.assertEqual(r.status_code,200,r.data);self.assertEqual(r.data['record']['status'],'PARTIAL')
        self.assertEqual(self.post('commission_receive',id=commission['id'],amount=61,received_on=str(timezone.localdate()),transaction_reference='INST-TX2',reason='Excess').status_code,400)
        r=self.post('commission_receive',id=commission['id'],amount=60,received_on=str(timezone.localdate()),transaction_reference='INST-TX2',reason='Balance received');self.assertEqual(r.status_code,200,r.data);self.assertEqual(r.data['record']['status'],'RECEIVED')
        self.assertEqual(self.post('commission_cancel',id=commission['id'],reason='Cancel received').status_code,400)

    def test_history_immutability_export_and_currency_separation(self):
        from django.db import DatabaseError,transaction
        from .models import FinanceEvent,FinanceFile,CommissionRule
        usd=self.invoice(currency='USD');npr=self.invoice(currency='NPR')
        for invoice in [usd,npr]:self.assertEqual(self.post('invoice_status',id=invoice['id'],status='ISSUED',reason='Charge agreed').status_code,200)
        payment=self.payment();self.upload('payments',payment['id']);self.verify(payment)
        for model in [FinanceEvent,FinanceFile]:
            with self.assertRaises(DatabaseError),transaction.atomic():model.objects.all().delete()
        self.assertEqual(self.post('rule_create',institution_id=self.uni.pk,name='Fixed commission',method='FIXED',value=100,currency='USD').status_code,201)
        with self.assertRaises(DatabaseError),transaction.atomic():CommissionRule.objects.update(value=200)
        self.assertEqual(self.post('expense_create',branch_id=self.branch.pk,category='Supplies',payee='=1+1',amount=50,currency='NPR',incurred_on=str(timezone.localdate())).status_code,201)
        response=self.client.get('/api/v1/finance/export/expenses/');self.assertEqual(response.status_code,200);self.assertIn("'=1+1",b''.join(response.streaming_content).decode('utf-8'))
        totals=self.client.get('/api/v1/finance/').data['summary'];self.assertEqual({r['currency'] for r in totals},{'USD','NPR'})
        self.assertEqual(Decimal(next(r for r in totals if r['currency']=='USD')['verified_payments']),Decimal(100))

    def test_invalid_inputs_and_idempotent_creation(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.post('invoice_status',id='invalid',status='ISSUED',reason='Invalid identifier').status_code,400)
        self.assertEqual(self.post('rule_create',institution_id=self.uni.pk,name='Invalid',method='PERCENTAGE',value=101,currency='USD').status_code,400)
        data={'action':'payment_create','person_id':str(self.person.pk),'amount':100,'currency':'USD','paid_on':str(timezone.localdate()),'method':'CASH'}
        for attempt in range(2):
            r=self.client.post('/api/v1/finance/',data,format='json',HTTP_IDEMPOTENCY_KEY='finance-payment-retry');self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(Payment.objects.count(),1)

    def test_proof_privacy_and_phase_three_deposit_integration(self):
        from progression.api import verified_deposit
        from django.core.files.uploadedfile import SimpleUploadedFile
        from crm.models import Branch,StaffProfile
        self.client.force_authenticate(self.admin);app=self.create();payment=self.payment(application_id=str(app.pk),purpose='DEPOSIT')
        r=self.client.post(f"/api/v1/finance/payments/{payment['id']}/proof/",{'file':SimpleUploadedFile('bad.html',b'<script/>')},format='multipart');self.assertEqual(r.status_code,400)
        item=self.upload('payments',payment['id']);self.upload('payments',payment['id']);self.verify(payment)
        self.assertEqual(verified_deposit(app,'USD'),Decimal('100.00'))
        self.client.force_authenticate(self.owner);result=self.client.get(f'/api/v1/progression/applications/{app.pk}/').data
        self.assertFalse(result['can_view_finance']);self.assertNotIn('payments',result)
        finance=self.user('finance-other','FINANCE');branch=Branch.objects.create(name='Hidden branch',code='FIN3');StaffProfile.objects.filter(user=finance).update(branch=branch);finance.refresh_from_db()
        self.client.force_authenticate(finance);self.assertEqual(self.client.get(f"/api/v1/finance/files/{item['id']}/").status_code,404)

    def test_issued_invoice_guard_and_financial_merge_reversal(self):
        from django.db import DatabaseError,transaction
        from crm.models import Person
        invoice=self.invoice()
        self.assertEqual(self.post('invoice_status',id=invoice['id'],status='ISSUED',reason='Approved charge').status_code,200)
        with self.assertRaises(DatabaseError),transaction.atomic():Invoice.objects.filter(pk=invoice['id']).update(amount=200)
        with self.assertRaises(DatabaseError),transaction.atomic():Invoice.objects.filter(pk=invoice['id']).update(status='DRAFT')
        survivor=Person.objects.create(ref='FIN-SURVIVOR',full_name='Canonical student',branch=self.branch,owner=self.owner,source=self.person.source)
        merged=self.client.post('/api/v1/merges/',{'survivor_id':str(survivor.pk),'merged_id':str(self.person.pk),'reason':'Confirmed duplicate'},format='json',HTTP_IDEMPOTENCY_KEY='financial-merge-guard');self.assertEqual(merged.status_code,200,merged.data)
        payment=self.post('payment_create',person_id=str(survivor.pk),amount=100,currency='USD',paid_on=str(timezone.localdate()),method='CASH').data['record']
        self.upload('payments',payment['id']);self.verify(payment)
        self.assertEqual(self.post('allocate',invoice_id=invoice['id'],payment_id=payment['id'],amount=100,reason='Settle moved invoice').status_code,200)
        reverse=self.client.post(f"/api/v1/merges/{merged.data['merge_id']}/reverse/",{'reason':'Attempt unsafe reversal'},format='json')
        self.assertEqual(reverse.status_code,400);self.assertEqual(Invoice.objects.get(pk=invoice['id']).person_id,survivor.pk)
        self.assertEqual(Invoice.objects.get(pk=invoice['id']).issued_snapshot['person_name'],'Student One')


from unittest import skipUnless
from django.db import connection,connections
from django.test import TransactionTestCase


@skipUnless(connection.vendor=='postgresql','Row-lock concurrency requires PostgreSQL')
class FinanceConcurrencyTests(TransactionTestCase):
    def test_parallel_allocations_cannot_spend_one_payment_twice(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.contrib.auth.models import User
        from crm.models import Branch,Person,Source
        branch=Branch.objects.create(name='Finance race branch',code='FRACE');actor=User.objects.create_user(username='finance-race')
        person=Person.objects.create(ref='FIN-RACE',full_name='Race student',branch=branch,owner=actor,source=Source.objects.create(name='Finance race'))
        invoices=[Invoice.objects.create(reference=f'INV-RACE-{i}',person=person,currency='USD',description='Charge',amount=100,status='ISSUED',created_by=actor) for i in range(2)]
        payment=Payment.objects.create(ref='PAY-RACE',person=person,amount=150,currency='USD',paid_on=timezone.localdate(),method='CASH',status='VERIFIED',created_by=actor)
        barrier=Barrier(2)
        def allocate(invoice):
            try:
                barrier.wait(timeout=10)
                allocate_payment(invoice.pk,payment.pk,100,actor)
                return 'allocated'
            except ValidationError:return 'rejected'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(allocate,invoices))
        self.assertEqual(sorted(results),['allocated','rejected'])
        self.assertEqual(sum(a.amount for a in payment.allocations.all()),Decimal(100))
