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
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
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
    status = models.CharField(max_length=20, default='QUEUED', choices=[(s, s.title()) for s in ['QUEUED', 'SENDING', 'SENT', 'DELIVERED', 'READ', 'FAILED', 'CANCELLED']])
    scheduled_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'scheduled_at'])]
