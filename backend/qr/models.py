import secrets
import uuid
from django.db import models
from django.conf import settings

def short_code():
    return secrets.token_urlsafe(9)

class QRCode(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    code=models.CharField(max_length=24,unique=True,default=short_code,editable=False)
    label=models.CharField(max_length=160)
    branch=models.ForeignKey('crm.Branch',on_delete=models.PROTECT)
    campaign=models.ForeignKey('crm.Campaign',on_delete=models.PROTECT)
    content_type=models.CharField(max_length=20,default='REGISTRATION')
    content=models.JSONField(default=dict)
    design=models.JSONField(default=dict)
    status=models.CharField(max_length=10,default='ACTIVE')
    expires_at=models.DateTimeField(null=True,blank=True)
    archived_at=models.DateTimeField(null=True,blank=True)
    scan_count=models.PositiveIntegerField(default=0)
    asset_version=models.PositiveIntegerField(default=0)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)

class QRAsset(models.Model):
    qr=models.ForeignKey(QRCode,on_delete=models.PROTECT,related_name='assets')
    version=models.PositiveIntegerField()
    png=models.FileField(upload_to='qr-assets/')
    svg=models.FileField(upload_to='qr-assets/')
    design=models.JSONField(default=dict)
    payload=models.TextField()
    decode_passed=models.BooleanField(default=False)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['qr','version'],name='unique_qr_asset_version')]
