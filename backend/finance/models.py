import uuid
from django.conf import settings
from django.db import models


class Invoice(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    reference=models.CharField(max_length=40,unique=True)
    person=models.ForeignKey('crm.Person',on_delete=models.PROTECT,related_name='invoices')
    application=models.ForeignKey('admissions.Application',on_delete=models.PROTECT,null=True,blank=True)
    currency=models.CharField(max_length=3)
    description=models.CharField(max_length=500)
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    due_on=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=20,default='DRAFT')
    issued_snapshot=models.JSONField(default=dict)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(amount__gt=0),name='invoice_amount_positive')]


class PaymentAllocation(models.Model):
    invoice=models.ForeignKey(Invoice,on_delete=models.PROTECT,related_name='allocations')
    payment=models.ForeignKey('progression.Payment',on_delete=models.PROTECT,related_name='allocations')
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(amount__gt=0),name='allocation_amount_positive'),models.UniqueConstraint(fields=['invoice','payment'],name='unique_invoice_payment_allocation')]


class Expense(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    reference=models.CharField(max_length=40,unique=True)
    branch=models.ForeignKey('crm.Branch',on_delete=models.PROTECT)
    category=models.CharField(max_length=80)
    payee=models.CharField(max_length=160)
    description=models.TextField(blank=True)
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    currency=models.CharField(max_length=3)
    incurred_on=models.DateField()
    status=models.CharField(max_length=20,default='PENDING')
    proof=models.ForeignKey('admissions.Document',on_delete=models.PROTECT,null=True,blank=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    verified_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(amount__gt=0),name='expense_amount_positive')]


class CommissionRule(models.Model):
    institution=models.ForeignKey('admissions.University',on_delete=models.PROTECT)
    name=models.CharField(max_length=160)
    version=models.PositiveIntegerField()
    method=models.CharField(max_length=20,choices=[('FIXED','Fixed'),('PERCENTAGE','Percentage')])
    value=models.DecimalField(max_digits=12,decimal_places=2)
    currency=models.CharField(max_length=3)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['institution','name','version'],name='unique_commission_rule_version'),models.CheckConstraint(condition=models.Q(value__gte=0),name='commission_rule_nonnegative')]


class InstitutionCommission(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    reference=models.CharField(max_length=40,unique=True)
    application=models.ForeignKey('admissions.Application',on_delete=models.PROTECT,related_name='institution_commissions')
    rule=models.ForeignKey(CommissionRule,on_delete=models.PROTECT)
    rule_snapshot=models.JSONField(default=dict)
    base_amount=models.DecimalField(max_digits=12,decimal_places=2)
    expected_amount=models.DecimalField(max_digits=12,decimal_places=2)
    currency=models.CharField(max_length=3)
    status=models.CharField(max_length=20,default='PENDING')
    due_on=models.DateField(null=True,blank=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(base_amount__gte=0),name='commission_base_nonnegative'),models.CheckConstraint(condition=models.Q(expected_amount__gte=0),name='commission_expected_nonnegative')]


class Receipt(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    reference=models.CharField(max_length=40,unique=True)
    payment=models.OneToOneField('progression.Payment',on_delete=models.PROTECT,related_name='receipt')
    snapshot=models.JSONField(default=dict)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)


class CommissionReceipt(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    reference=models.CharField(max_length=40,unique=True)
    commission=models.ForeignKey(InstitutionCommission,on_delete=models.PROTECT,related_name='receipts')
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    received_on=models.DateField()
    transaction_reference=models.CharField(max_length=120)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(amount__gt=0),name='commission_receipt_positive')]


class FinanceFile(models.Model):
    payment=models.ForeignKey('progression.Payment',on_delete=models.PROTECT,null=True,blank=True,related_name='financial_files')
    expense=models.ForeignKey(Expense,on_delete=models.PROTECT,null=True,blank=True,related_name='financial_files')
    commission_receipt=models.ForeignKey(CommissionReceipt,on_delete=models.PROTECT,null=True,blank=True,related_name='financial_files')
    filename=models.CharField(max_length=255)
    mime=models.CharField(max_length=80)
    content=models.BinaryField()
    checksum=models.CharField(max_length=64)
    uploaded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=(models.Q(payment__isnull=False,expense__isnull=True,commission_receipt__isnull=True)|models.Q(payment__isnull=True,expense__isnull=False,commission_receipt__isnull=True)|models.Q(payment__isnull=True,expense__isnull=True,commission_receipt__isnull=False)),name='finance_file_one_owner')]


class FinanceEvent(models.Model):
    object_kind=models.CharField(max_length=30)
    object_id=models.CharField(max_length=40)
    branch=models.ForeignKey('crm.Branch',on_delete=models.PROTECT,null=True,blank=True)
    action=models.CharField(max_length=60)
    old=models.JSONField(default=dict)
    new=models.JSONField(default=dict)
    reason=models.TextField(blank=True)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        indexes=[models.Index(fields=['object_kind','object_id','created_at'])]
