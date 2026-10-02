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
