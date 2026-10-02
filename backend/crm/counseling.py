from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from .idempotency import idempotent
from .models import CounselingRecord, FollowUp, Task
from .operations import object_person
from .services import activity, audit, refresh_next_action, notify

DOCUMENT_TYPES = ['Passport','Academic Transcripts','Certificates','Citizenship / ID','English Test Score','CV / Resume','Financial Documents','Photographs','Recommendation Letters','Experience Letters','Medical / Police Report','Other']

class RecordInput(serializers.Serializer):
    summary = serializers.CharField(max_length=10000, allow_blank=True)
    decisions = serializers.CharField(max_length=10000, allow_blank=True)
    documents_checklist = serializers.ListField(child=serializers.ChoiceField(choices=DOCUMENT_TYPES), max_length=12)
    pending_documents = serializers.CharField(max_length=5000, allow_blank=True)
    next_step = serializers.CharField(max_length=200, allow_blank=True)
    service_taken = serializers.CharField(max_length=200, allow_blank=True)
    remarks = serializers.CharField(max_length=5000, allow_blank=True)
    followup_due_at = serializers.DateTimeField(required=False)
    followup_owner_id = serializers.IntegerField(required=False)
    followup_method = serializers.ChoiceField(choices=['CALL','WHATSAPP','EMAIL','MEETING'], required=False, default='CALL')
    followup_notes = serializers.CharField(max_length=5000, allow_blank=True, required=False)
    scanning_owner_id = serializers.IntegerField(required=False)
    scanning_due_at = serializers.DateTimeField(required=False)


def assignee(person, user_id, roles):
    return get_object_or_404(User, pk=user_id, is_active=True, staff__branch=person.branch, staff__role__in=roles, staff__availability='ACTIVE')


@extend_schema(request=RecordInput, responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
@idempotent()
def record(request, pk):
    person = object_person(request, pk, 'view' if request.method=='GET' else 'counseling', request.method!='GET')
    fields = ['summary','decisions','documents_checklist','pending_documents','next_step','service_taken','remarks']
    item = CounselingRecord.objects.filter(person=person).select_related('updated_by').first()
    if request.method=='POST':
        if set(request.data)-set(RecordInput().fields):raise ValidationError('Unsupported counseling fields.')
        serializer = RecordInput(data=request.data); serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        followup_owner = None; scan_owner = None
        if data.get('followup_due_at'):
            if not data['next_step'].strip():raise ValidationError('Add the next step before scheduling a follow-up.')
            followup_owner = assignee(person, data.get('followup_owner_id', request.user.pk), ['ADMIN','MANAGER','COUNSELOR','FRONTDESK'])
        elif data.get('followup_owner_id'):raise ValidationError('Choose a follow-up date.')
        if data.get('scanning_owner_id'):
            if not data.get('scanning_due_at'):raise ValidationError('Choose when scanning is due.')
            scan_owner = assignee(person, data['scanning_owner_id'], ['FRONTDESK'])
        elif data.get('scanning_due_at'):raise ValidationError('Choose the frontdesk officer for scanning.')
        old = {field:getattr(item,field) for field in fields} if item else {}
        item, _ = CounselingRecord.objects.update_or_create(person=person, defaults={**{field:data[field] for field in fields},'updated_by':request.user})
        if followup_owner:
            followup = FollowUp.objects.create(person=person, owner=followup_owner, subject=data['next_step'], due_at=data['followup_due_at'], method=data['followup_method'], notes=data.get('followup_notes',''))
            notify(followup_owner, 'COUNSELING_FOLLOWUP', 'Counselor assigned a follow-up', person=person, message=followup.notes)
        if scan_owner:
            Task.objects.create(person=person,owner=scan_owner,title='Scan and upload visitor documents',due_at=data['scanning_due_at'])
            notify(scan_owner,'DOCUMENT_SCANNING','Documents ready for scanning',person=person,message=data['pending_documents'])
        refresh_next_action(person)
        activity(request,person,'Counseling record updated',data['summary']+'\n'+data['decisions'],'MEETING')
        audit(request,'COUNSELING_UPDATED',person,old=old,new={field:data[field] for field in fields})
    return Response({**{field:getattr(item,field) if item else ([] if field=='documents_checklist' else '') for field in fields},'updated_at':item.updated_at if item else None,'updated_by':item.updated_by.get_full_name() if item and item.updated_by else '', 'assigned_counselor':person.owner.get_full_name() if person.owner else 'Unassigned'})
