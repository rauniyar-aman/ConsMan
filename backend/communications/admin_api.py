import os
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Count
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from crm.models import Person,Branch,Source,ContactMethod
from crm.permissions import scope
from crm.services import audit,next_reference,normalize_phone,duplicate_signals
from crm.operations import required_reason
from crm.idempotency import idempotent
from .models import AutomationRule,AutomationRun,LeadForm,SocialLead,InboundMessage,WebhookReceipt,OutboundMessage,MessageTemplate,WorkerHeartbeat
from .providers import configured
from .dispatch import event


def number(data,key):return serializers.IntegerField(min_value=1).run_validation(data.get(key))
def string(data,key,length=120):return serializers.CharField(max_length=length).run_validation(data.get(key))
def boolean(data,key,default=False):return serializers.BooleanField().run_validation(data.get(key,default))


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
@idempotent()
def workspace(request):
    scope(request.user,'communication_templates')
    if request.method=='GET':
        from datetime import timedelta
        heartbeat=WorkerHeartbeat.objects.filter(name='communications').first()
        return Response({'outbound_stats':list(OutboundMessage.objects.values('status').annotate(count=Count('pk'))),'uncertain':list(OutboundMessage.objects.filter(status='UNKNOWN').values('id','person__full_name','channel','recipient','body','error_code')[:100]),'providers':{c:configured(c) for c in ['EMAIL','SMS','WHATSAPP']},'worker_seen_at':heartbeat.seen_at if heartbeat else None,'worker_healthy':bool(heartbeat and heartbeat.seen_at>timezone.now()-timedelta(minutes=3)),'broker_configured':bool(os.getenv('CELERY_BROKER_URL')),'rules':list(AutomationRule.objects.order_by('-pk').values()[:200]),'runs':list(AutomationRun.objects.order_by('-pk').values()[:100]),'forms':list(LeadForm.objects.order_by('-pk').values()[:200]),'leads':list(SocialLead.objects.order_by('-pk').values()[:100]),'unmatched':list(InboundMessage.objects.filter(person__isnull=True).order_by('-received_at').values()[:100]),'webhooks':list(WebhookReceipt.objects.order_by('-pk').values('id','provider','status','attempts','error_code','created_at')[:100]),'templates':list(MessageTemplate.objects.order_by('name','-version').values('id','name','version','channel')[:500]),'branches':list(Branch.objects.values('id','name')),'sources':list(Source.objects.values('id','name'))})
    data=request.data;action=data.get('action')
    if action=='rule_create':
        trigger=serializers.ChoiceField(choices=['FOLLOWUP_DUE','TASK_DUE','APPLICATION_DEADLINE','APPLICATION_STATE']).run_validation(data.get('trigger'))
        student=boolean(data,'student_messages');staff=boolean(data,'staff_notifications',True)
        template=get_object_or_404(MessageTemplate,pk=number(data,'template_id')) if data.get('template_id') else None
        if student and not template:raise ValidationError('Student automation needs a template.')
        if not student and not staff:raise ValidationError('Select at least one recipient type.')
        obj=AutomationRule.objects.create(name=string(data,'name'),branch=get_object_or_404(Branch,pk=number(data,'branch_id')),trigger=trigger,template=template,student_messages=student,staff_notifications=staff,advance_minutes=serializers.IntegerField(min_value=0,max_value=10080).run_validation(data.get('advance_minutes',60)),starts_at=timezone.now(),created_by=request.user)
    elif action=='rule_toggle':
        obj=get_object_or_404(AutomationRule.objects.select_for_update(),pk=number(data,'id'));obj.enabled=boolean(data,'enabled');obj.save(update_fields=['enabled'])
    elif action=='form_create':
        obj=LeadForm.objects.create(form_id=serializers.RegexField(r'^\d{1,100}$').run_validation(data.get('form_id')),page_id=serializers.RegexField(r'^\d{1,100}$').run_validation(data.get('page_id')),platform=serializers.ChoiceField(choices=['FACEBOOK','INSTAGRAM']).run_validation(data.get('platform')),name=string(data,'name'),branch=get_object_or_404(Branch,pk=number(data,'branch_id')),source=get_object_or_404(Source,pk=number(data,'source_id')),created_by=request.user)
    elif action=='form_toggle':
        obj=get_object_or_404(LeadForm.objects.select_for_update(),pk=number(data,'id'));obj.enabled=boolean(data,'enabled');obj.save(update_fields=['enabled'])
    elif action=='lead_review':
        obj=get_object_or_404(SocialLead.objects.select_for_update().select_related('form'),pk=number(data,'id'))
        if obj.status!='REVIEW':raise ValidationError('Only fetched, unreviewed leads can be processed.')
        reason=required_reason(data)
        if data.get('reject'):
            obj.status='REJECTED'
        else:
            fields={str(f.get('name')):str((f.get('values') or [''])[0]) for f in obj.payload.get('field_data',[]) if isinstance(f,dict)}
            name=fields.get('full_name') or ' '.join(filter(None,[fields.get('first_name'),fields.get('last_name')]))
            phone=fields.get('phone_number','');email=fields.get('email','').strip().lower()
            if data.get('person_id'):person=get_object_or_404(Person,pk=serializers.UUIDField().run_validation(data.get('person_id')),archived_at__isnull=True,merged_into__isnull=True,branch=obj.form.branch)
            else:
                name=serializers.CharField(max_length=160).run_validation(name)
                if not phone and not email:raise ValidationError('A lead contact is required.')
                normalized=normalize_phone(phone) if phone else ''
                if email:email=serializers.EmailField().run_validation(email)
                if duplicate_signals(normalized,email,name):raise ValidationError('Potential duplicate: link the reviewed lead to an existing person.')
                person=Person.objects.create(ref=next_reference(),full_name=name,branch=obj.form.branch,source=obj.form.source,created_by=request.user)
                if normalized:ContactMethod.objects.create(person=person,type='PHONE',raw_value=phone,normalized_value=normalized,is_primary=True)
                if email:ContactMethod.objects.create(person=person,type='EMAIL',raw_value=email,normalized_value=email,is_primary=True)
            obj.person=person;obj.status='ACCEPTED'
            audit(request,'SOCIAL_LEAD_ACCEPTED',person,new={'lead_id':obj.lead_id,'form_id':obj.form.form_id,'reason':reason})
        obj.reviewed_by=request.user;obj.save(update_fields=['person','status','reviewed_by'])
    elif action=='inbound_match':
        obj=get_object_or_404(InboundMessage.objects.select_for_update(),pk=serializers.UUIDField().run_validation(data.get('id')))
        if obj.person_id:raise ValidationError('Message is already matched.')
        person=get_object_or_404(Person,pk=serializers.UUIDField().run_validation(data.get('person_id')),archived_at__isnull=True,merged_into__isnull=True)
        required_reason(data)
        if not person.contacts.filter(normalized_value=obj.sender).exists():raise ValidationError('Sender does not match this person contact.')
        obj.person=person;obj.save(update_fields=['person']);audit(request,'INBOUND_MESSAGE_MATCHED',person,new={'message_id':str(obj.pk)})
    elif action=='webhook_retry':
        obj=get_object_or_404(WebhookReceipt.objects.select_for_update(),pk=number(data,'id'));required_reason(data)
        if obj.status=='PROCESSED':raise ValidationError('Already processed.')
        obj.attempts=0;obj.error_code='';obj.save(update_fields=['attempts','error_code'])
    elif action=='lead_retry':
        obj=get_object_or_404(SocialLead.objects.select_for_update(),pk=number(data,'id'));required_reason(data)
        if obj.status!='PENDING':raise ValidationError('Only pending leads can be retrieved again.')
        obj.attempts=0;obj.error_code='';obj.save(update_fields=['attempts','error_code'])
    elif action=='message_reconcile':
        obj=get_object_or_404(OutboundMessage.objects.select_for_update(),pk=serializers.UUIDField().run_validation(data.get('id')))
        if obj.status!='UNKNOWN':raise ValidationError('Only uncertain sends need reconciliation.')
        status=serializers.ChoiceField(choices=['SENT','FAILED']).run_validation(data.get('status'));reason=required_reason(data)
        if data.get('provider_id'):obj.provider_id=string(data,'provider_id',200)
        event(obj,status,'MANUAL_RECONCILIATION');audit(request,'MESSAGE_RECONCILED',obj.person,new={'id':str(obj.pk),'status':status,'reason':reason})
    else:raise ValidationError('Unknown configuration action.')
    audit(request,'COMMUNICATION_CONFIG_CHANGED',obj,new={'action':action})
    return Response({'id':str(obj.pk)},status=201)
