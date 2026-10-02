import uuid
from django.conf import settings
from django.db import models


class PrivateLink(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    person=models.ForeignKey('crm.Person',on_delete=models.PROTECT)
    token_hash=models.CharField(max_length=64,unique=True)
    expires_at=models.DateTimeField()
    active=models.BooleanField(default=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)


class StudentRequest(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    person=models.ForeignKey('crm.Person',on_delete=models.PROTECT,related_name='student_requests')
    title=models.CharField(max_length=160)
    instructions=models.TextField()
    due_at=models.DateTimeField(null=True,blank=True)
    status=models.CharField(max_length=20,default='OPEN')
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)


class StudentMessage(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    person=models.ForeignKey('crm.Person',on_delete=models.PROTECT,related_name='student_messages')
    direction=models.CharField(max_length=10)
    body=models.TextField()
    sender=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True)
    access=models.ForeignKey(PrivateLink,on_delete=models.PROTECT,null=True,blank=True)
    request=models.ForeignKey(StudentRequest,on_delete=models.PROTECT,null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
