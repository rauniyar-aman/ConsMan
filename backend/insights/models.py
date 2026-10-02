import uuid
from django.conf import settings
from django.db import models


class AssistanceReview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey('crm.Person', on_delete=models.PROTECT)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=30)
    decision = models.CharField(max_length=20)
    evidence = models.JSONField()
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class AssistanceGeneration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey('crm.Person', on_delete=models.PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
