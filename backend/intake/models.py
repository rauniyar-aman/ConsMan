import uuid
from django.db import models
from django.conf import settings

class IntakeSubmission(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    qr=models.ForeignKey('qr.QRCode',on_delete=models.PROTECT,related_name='submissions')
    payload=models.JSONField(default=dict)
    phone_e164=models.CharField(max_length=40)
    channel_choice=models.CharField(max_length=20,default='SMS')
    status=models.CharField(max_length=20,default='UNVERIFIED',db_index=True)
    outcome=models.CharField(max_length=20,blank=True)
    unverified_reason=models.CharField(max_length=30,default='NOT_ENTERED')
    person=models.ForeignKey('crm.Person',on_delete=models.PROTECT,null=True,blank=True)
    resume_token_hash=models.CharField(max_length=64)
    resume_expires_at=models.DateTimeField()
    ip_hash=models.CharField(max_length=64)
    idempotency_key=models.CharField(max_length=160,unique=True)
    request_hash=models.CharField(max_length=64)
    verified_at=models.DateTimeField(null=True,blank=True)
    verified_by_staff=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True)
    verified_via=models.CharField(max_length=30,blank=True)
    corrections=models.PositiveIntegerField(default=0)
    send_count=models.PositiveIntegerField(default=0)
    purge_after=models.DateTimeField()
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)

class OtpChallenge(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    submission=models.ForeignKey(IntakeSubmission,on_delete=models.PROTECT,related_name='challenges')
    phone_e164=models.CharField(max_length=40)
    channel=models.CharField(max_length=20)
    salt=models.CharField(max_length=40)
    code_hash=models.CharField(max_length=64)
    expires_at=models.DateTimeField()
    attempts=models.PositiveIntegerField(default=0)
    last_sent_at=models.DateTimeField()
    status=models.CharField(max_length=20,default='PENDING')

class MessageDelivery(models.Model):
    challenge=models.OneToOneField(OtpChallenge,on_delete=models.PROTECT)
    provider=models.CharField(max_length=30)
    channel=models.CharField(max_length=20)
    to_hash=models.CharField(max_length=64)
    content_hash=models.CharField(max_length=64,blank=True)
    provider_message_id=models.CharField(max_length=160,blank=True,db_index=True)
    status=models.CharField(max_length=20,default='QUEUED')
    error=models.CharField(max_length=60,blank=True)
    cost_minor=models.PositiveIntegerField(default=0)
    created_at=models.DateTimeField(auto_now_add=True)

class RateBucket(models.Model):
    key=models.CharField(max_length=180,unique=True)
    count=models.PositiveIntegerField(default=0)
    expires_at=models.DateTimeField()

class RateEvent(models.Model):
    key=models.CharField(max_length=180,db_index=True)
    amount=models.PositiveIntegerField(default=1)
    at=models.DateTimeField(auto_now_add=True,db_index=True)

class IntakeReview(models.Model):
    submission=models.OneToOneField(IntakeSubmission,on_delete=models.PROTECT)
    candidate_ids=models.JSONField(default=list)
    confidence=models.CharField(max_length=10)
    resolved_at=models.DateTimeField(null=True)
