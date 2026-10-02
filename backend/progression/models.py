import uuid
from django.conf import settings
from django.db import models


class JourneyCounter(models.Model):
    kind=models.CharField(max_length=10)
    year=models.PositiveIntegerField()
    value=models.PositiveIntegerField(default=0)
    class Meta:constraints=[models.UniqueConstraint(fields=['kind','year'],name='unique_journey_counter')]


class VisaWorkflow(models.Model):
    country=models.CharField(max_length=80)
    version=models.PositiveIntegerField()
    milestones=models.JSONField(default=list)
    required_documents=models.JSONField(default=list)
    predeparture_checklist=models.JSONField(default=list)
    financial_evidence_required=models.BooleanField(default=True)
    active=models.BooleanField(default=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['country','version'],name='unique_visa_workflow_version')]


class OfferCondition(models.Model):
    offer=models.ForeignKey('admissions.OfferReceipt',on_delete=models.PROTECT,related_name='tracked_conditions')
    title=models.CharField(max_length=500)
    status=models.CharField(max_length=20,default='PENDING')
    document=models.ForeignKey('admissions.Document',on_delete=models.PROTECT,null=True,blank=True)
    reason=models.TextField(blank=True)
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True)
    reviewed_at=models.DateTimeField(null=True,blank=True)


class DepositRequirement(models.Model):
    application=models.OneToOneField('admissions.Application',on_delete=models.PROTECT,related_name='deposit_requirement')
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    currency=models.CharField(max_length=3)
    due_at=models.DateTimeField(null=True,blank=True)
    waived=models.BooleanField(default=False)
    reason=models.TextField(blank=True)


class Payment(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    ref=models.CharField(max_length=24,unique=True)
    person=models.ForeignKey('crm.Person',on_delete=models.PROTECT,related_name='payments')
    application=models.ForeignKey('admissions.Application',on_delete=models.PROTECT,related_name='payments',null=True,blank=True)
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    currency=models.CharField(max_length=3)
    paid_on=models.DateField()
    method=models.CharField(max_length=30)
    reference=models.CharField(max_length=120,blank=True)
    status=models.CharField(max_length=20,default='PENDING')
    purpose=models.CharField(max_length=30,default='DEPOSIT')
    proof=models.ForeignKey('admissions.Document',on_delete=models.PROTECT,null=True,blank=True)
    verified_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.CheckConstraint(condition=models.Q(amount__gt=0),name='payment_amount_positive')]


class EnrollmentDocument(models.Model):
    application=models.ForeignKey('admissions.Application',on_delete=models.PROTECT,related_name='enrollment_documents')
    kind=models.CharField(max_length=30)
    number=models.CharField(max_length=120)
    issued_on=models.DateField()
    expires_at=models.DateTimeField(null=True,blank=True)
    document=models.ForeignKey('admissions.Document',on_delete=models.PROTECT)
    status=models.CharField(max_length=20,default='ISSUED')
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['application','kind','number'],name='unique_enrollment_document')]


class VisaCase(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    ref=models.CharField(max_length=24,unique=True)
    application=models.ForeignKey('admissions.Application',on_delete=models.PROTECT,related_name='visa_cases')
    attempt_no=models.PositiveIntegerField()
    previous_case=models.ForeignKey('self',on_delete=models.PROTECT,null=True,blank=True)
    state=models.CharField(max_length=30,default='PREPARING')
    workflow_snapshot=models.JSONField(default=dict)
    appointment_at=models.DateTimeField(null=True,blank=True)
    submitted_at=models.DateTimeField(null=True,blank=True)
    decided_at=models.DateTimeField(null=True,blank=True)
    decision_reason=models.TextField(blank=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['application','attempt_no'],name='unique_visa_attempt')]


class VisaDocument(models.Model):
    case=models.ForeignKey(VisaCase,on_delete=models.PROTECT,related_name='checklist_documents')
    document=models.OneToOneField('admissions.Document',on_delete=models.PROTECT)


class VisaBlocker(models.Model):
    case=models.ForeignKey(VisaCase,on_delete=models.PROTECT,related_name='case_blockers')
    blocker=models.OneToOneField('admissions.Blocker',on_delete=models.PROTECT)


class VisaEvent(models.Model):
    case=models.ForeignKey(VisaCase,on_delete=models.PROTECT,related_name='events')
    type=models.CharField(max_length=40)
    old=models.JSONField(default=dict)
    new=models.JSONField(default=dict)
    reason=models.TextField(blank=True)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)


class FinancialEvidence(models.Model):
    case=models.ForeignKey(VisaCase,on_delete=models.PROTECT,related_name='financial_evidence')
    document=models.ForeignKey('admissions.Document',on_delete=models.PROTECT)
    kind=models.CharField(max_length=40)
    holder=models.CharField(max_length=160)
    amount=models.DecimalField(max_digits=14,decimal_places=2)
    currency=models.CharField(max_length=3)
    statement_on=models.DateField()
    expires_at=models.DateTimeField(null=True,blank=True)
    status=models.CharField(max_length=20,default='PENDING')
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True)
    reason=models.TextField(blank=True)


class PreDeparture(models.Model):
    application=models.OneToOneField('admissions.Application',on_delete=models.PROTECT,related_name='predeparture')
    checklist=models.JSONField(default=list)
    accommodation=models.TextField(blank=True)
    airport_pickup=models.CharField(max_length=255,blank=True)
    flight_number=models.CharField(max_length=40,blank=True)
    departure_at=models.DateTimeField(null=True,blank=True)
    arrival_at=models.DateTimeField(null=True,blank=True)
    arrived_at=models.DateTimeField(null=True,blank=True)
    notes=models.TextField(blank=True)


class Enrollment(models.Model):
    application=models.OneToOneField('admissions.Application',on_delete=models.PROTECT,related_name='enrollment')
    enrolled_on=models.DateField()
    institution_reference=models.CharField(max_length=120)
    confirmed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    confirmed_at=models.DateTimeField(auto_now_add=True)
