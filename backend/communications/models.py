import uuid
from django.conf import settings
from django.db import models


CHANNELS = [('EMAIL', 'Email'), ('WHATSAPP', 'WhatsApp'), ('SMS', 'SMS')]


class MessageTemplate(models.Model):
    name = models.CharField(max_length=120)
    version = models.PositiveIntegerField(default=1)
    channel = models.CharField(max_length=12, choices=CHANNELS)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['name', 'channel', 'version'], name='communication_template_version')]


class ChannelConsent(models.Model):
    person = models.ForeignKey('crm.Person', on_delete=models.PROTECT)
    channel = models.CharField(max_length=12, choices=CHANNELS)
    allowed = models.BooleanField(default=False)
    evidence = models.CharField(max_length=500)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['person', 'channel', '-id'])]


class OutboundMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey('crm.Person', on_delete=models.PROTECT)
    contact = models.ForeignKey('crm.ContactMethod', on_delete=models.PROTECT)
    template = models.ForeignKey(MessageTemplate, on_delete=models.PROTECT)
    consent = models.ForeignKey(ChannelConsent, on_delete=models.PROTECT)
    channel = models.CharField(max_length=12, choices=CHANNELS)
    recipient = models.CharField(max_length=254)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=20, default='QUEUED', choices=[(s, s.title()) for s in ['QUEUED', 'SENDING', 'SENT', 'DELIVERED', 'READ', 'FAILED', 'UNKNOWN', 'CANCELLED']])
    scheduled_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'scheduled_at'])]

    attempts = models.PositiveSmallIntegerField(default=0)
    provider_id = models.CharField(max_length=200, blank=True, db_index=True)
    error_code = models.CharField(max_length=60, blank=True)
    claimed_at = models.DateTimeField(null=True)
    dedupe_key = models.CharField(max_length=200, unique=True, null=True)


class MessageEvent(models.Model):
    message = models.ForeignKey(OutboundMessage, on_delete=models.PROTECT, related_name='events')
    status = models.CharField(max_length=20)
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    estimated_cost_minor = models.PositiveIntegerField(default=0)


class InboundMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey('crm.Person', on_delete=models.PROTECT, null=True)
    channel = models.CharField(max_length=12, choices=CHANNELS)
    sender = models.CharField(max_length=254)
    body = models.TextField()
    provider_id = models.CharField(max_length=200)
    received_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['channel','provider_id'], name='unique_inbound_provider_message')]


class WebhookReceipt(models.Model):
    provider = models.CharField(max_length=20)
    event_key = models.CharField(max_length=64)
    payload = models.JSONField()
    status = models.CharField(max_length=20, default='PENDING')
    error_code = models.CharField(max_length=60, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['provider','event_key'], name='unique_communication_webhook')]


class AutomationRule(models.Model):
    name = models.CharField(max_length=120)
    branch = models.ForeignKey('crm.Branch', on_delete=models.PROTECT)
    trigger = models.CharField(max_length=30, choices=[(x,x) for x in ['FOLLOWUP_DUE','TASK_DUE','APPLICATION_DEADLINE','APPLICATION_STATE']])
    template = models.ForeignKey(MessageTemplate, on_delete=models.PROTECT, null=True)
    student_messages = models.BooleanField(default=False)
    staff_notifications = models.BooleanField(default=True)
    advance_minutes = models.PositiveIntegerField(default=60)
    enabled = models.BooleanField(default=False)
    starts_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class AutomationRun(models.Model):
    rule = models.ForeignKey(AutomationRule, on_delete=models.PROTECT)
    event_key = models.CharField(max_length=200)
    outcome = models.CharField(max_length=60)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['rule','event_key'], name='unique_communication_automation')]


class LeadForm(models.Model):
    form_id = models.CharField(max_length=100, unique=True)
    page_id = models.CharField(max_length=100)
    platform = models.CharField(max_length=12, choices=[('FACEBOOK','Facebook'),('INSTAGRAM','Instagram')])
    name = models.CharField(max_length=120)
    branch = models.ForeignKey('crm.Branch', on_delete=models.PROTECT)
    source = models.ForeignKey('crm.Source', on_delete=models.PROTECT)
    enabled = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class SocialLead(models.Model):
    lead_id = models.CharField(max_length=100, unique=True)
    form = models.ForeignKey(LeadForm, on_delete=models.PROTECT)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='PENDING')
    person = models.ForeignKey('crm.Person', on_delete=models.PROTECT, null=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error_code = models.CharField(max_length=60, blank=True)


class WorkerHeartbeat(models.Model):
    name = models.CharField(max_length=40, primary_key=True)
    seen_at = models.DateTimeField()
