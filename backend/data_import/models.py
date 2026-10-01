import uuid
from django.db import models
from django.conf import settings

class ImportBatch(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    source_file=models.CharField(max_length=255)
    file_hash=models.CharField(max_length=64)
    branch=models.ForeignKey('crm.Branch',on_delete=models.PROTECT)
    file=models.FileField(upload_to='import-originals/')
    mapping=models.JSONField(default=dict)
    defaults=models.JSONField(default=dict)
    status=models.CharField(max_length=20,default='STAGING')
    counts=models.JSONField(default=dict)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    approved_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+',null=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['file_hash','branch'],name='unique_import_file_branch')]

class ImportRow(models.Model):
    batch=models.ForeignKey(ImportBatch,on_delete=models.PROTECT,related_name='rows')
    row_no=models.PositiveIntegerField()
    raw=models.JSONField(default=dict)
    normalized=models.JSONField(default=dict)
    result=models.CharField(max_length=20,default='STAGED')
    errors=models.JSONField(default=list)
    candidates=models.JSONField(default=list)
    override_reason=models.CharField(max_length=500,blank=True)
    person=models.ForeignKey('crm.Person',on_delete=models.PROTECT,null=True)
    committed_person_updated_at=models.DateTimeField(null=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['batch','row_no'],name='unique_import_row')]
