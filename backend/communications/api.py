from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from crm.idempotency import idempotent
from crm.models import Person
from crm.permissions import scope, scoped_people
from crm.services import audit
from progression.models import JourneyCounter
from .models import CHANNELS, ChannelConsent, MessageTemplate, OutboundMessage
from .services import queue, render,display_body
from .dispatch import event
from .models import InboundMessage
from django.db.models import Q


def channel(data):
    return serializers.ChoiceField(choices=[c[0] for c in CHANNELS]).run_validation(data.get('channel'))


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(['GET', 'POST'])
@transaction.atomic
@idempotent()
def templates(request):
    scope(request.user, 'communication_view')
    if request.method == 'GET':
        return Response({'results': list(MessageTemplate.objects.order_by('name', '-version').values('id', 'name', 'version', 'channel', 'subject', 'body')[:500])})
    scope(request.user, 'communication_templates')
    data = request.data
    code = channel(data)
    name = serializers.CharField(max_length=120).run_validation(data.get('name'))
    body = serializers.CharField(max_length=5000).run_validation(data.get('body'))
    subject = serializers.CharField(max_length=200, allow_blank=True).run_validation(data.get('subject', ''))
    if '\r' in subject or '\n' in subject:raise ValidationError('Subject must be a single line.')
    lock, _ = JourneyCounter.objects.get_or_create(kind='MSG_TPL', year=0)
    JourneyCounter.objects.select_for_update().get(pk=lock.pk)
    version = (MessageTemplate.objects.filter(name=name, channel=code).aggregate(v=Max('version'))['v'] or 0) + 1
    obj = MessageTemplate(name=name, channel=code, version=version, body=body, subject=subject, created_by=request.user)
    # Check placeholders without requiring a real person's data.
    import re
    fields = re.findall(r'\{\{\s*([a-z_]+)\s*\}\}', body + subject)
    if any(f not in ['student_name', 'person_reference', 'branch_name','due_at','event_title','application_reference','application_state'] for f in fields):
        raise ValidationError('Use supported student, branch, due_at, event_title or application placeholders.')
    obj.save()
    audit(request, 'MESSAGE_TEMPLATE_CREATED', obj, new={'name': name, 'version': version, 'channel': code})
    return Response({'id': obj.pk, 'version': version}, status=201)


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(['GET', 'POST'])
@transaction.atomic
@idempotent()
def person_messages(request, pk):
    scope(request.user, 'communication_send' if request.method == 'POST' else 'communication_view')
    person = get_object_or_404(scoped_people(request.user, Person.objects.all()), pk=pk)
    if request.method == 'GET':
        related=Q(person=person)|Q(person__merged_into=person)
        offset=serializers.IntegerField(min_value=0).run_validation(request.query_params.get('offset',0))
        outgoing=OutboundMessage.objects.filter(related);incoming=InboundMessage.objects.filter(related)
        messages=outgoing.prefetch_related('events').order_by('-created_at')[offset:offset+100]
        consents=[]
        for code,_ in CHANNELS:
            latest=ChannelConsent.objects.filter(person=person,channel=code).order_by('-pk').values('id','channel','allowed','evidence','created_at').first()
            if latest:consents.append(latest)
        incoming_rows=list(incoming.order_by('-received_at').values()[offset:offset+100])
        for row in incoming_rows:row['body']=display_body(row['body'])
        return Response({'has_more':offset+100<max(outgoing.count(),incoming.count()),'consents':consents, 'messages':[{'id':str(m.pk),'channel':m.channel,'recipient':m.recipient,'subject':m.subject,'body':display_body(m.body),'status':m.status,'error_code':m.error_code,'scheduled_at':m.scheduled_at,'created_at':m.created_at,'events':list(m.events.values('status','detail','created_at'))} for m in messages], 'incoming':incoming_rows})
    Person.objects.select_for_update().get(pk=person.pk)
    data = request.data
    if data.get('action') == 'consent':
        code = channel(data)
        allowed = serializers.BooleanField().run_validation(data.get('allowed'))
        evidence = serializers.CharField(max_length=500).run_validation(data.get('evidence'))
        obj = ChannelConsent.objects.create(person=person, channel=code, allowed=allowed, evidence=evidence, recorded_by=request.user)
        if not allowed:
            for message in OutboundMessage.objects.select_for_update().filter(person=person,channel=code,status='QUEUED'):event(message,'CANCELLED','CONSENT_WITHDRAWN')
        audit(request, 'MESSAGE_CONSENT_RECORDED', person, new={'channel': code, 'allowed': allowed, 'consent_id': obj.pk})
        return Response({'id': obj.pk}, status=201)
    if data.get('action') in ['preview', 'queue']:
        template = get_object_or_404(MessageTemplate, pk=serializers.IntegerField(min_value=1).run_validation(data.get('template_id')))
        if data['action'] == 'preview':
            subject, body = render(template, person)
            return Response({'subject': subject, 'body': body})
        contact = get_object_or_404(person.contacts, pk=serializers.IntegerField(min_value=1).run_validation(data.get('contact_id')))
        scheduled = serializers.DateTimeField().run_validation(data['scheduled_at']) if data.get('scheduled_at') else timezone.now()
        obj = queue(person, contact, template, request.user, scheduled)
        audit(request, 'MESSAGE_QUEUED', person, new={'message_id': str(obj.pk), 'channel': obj.channel})
        return Response({'id': obj.pk, 'status': obj.status}, status=201)
    if data.get('action') == 'cancel':
        obj = get_object_or_404(OutboundMessage.objects.select_for_update(), person=person, pk=serializers.UUIDField().run_validation(data.get('message_id')))
        if obj.status != 'QUEUED':
            raise ValidationError('Only queued messages can be cancelled.')
        event(obj,'CANCELLED','STAFF_CANCELLED')
        audit(request, 'MESSAGE_CANCELLED', person, new={'message_id': str(obj.pk)})
        return Response({'status': obj.status})
    if data.get('action')=='retry':
        from crm.operations import required_reason
        from .services import eligible
        obj=get_object_or_404(OutboundMessage.objects.select_for_update().select_related('person','contact'),person=person,pk=serializers.UUIDField().run_validation(data.get('message_id')))
        if obj.status!='FAILED' or not eligible(obj):raise ValidationError('Only definitely failed messages with valid consent can be retried.')
        reason=required_reason(data);obj.attempts=0;obj.scheduled_at=timezone.now();event(obj,'QUEUED','MANUAL_RETRY')
        audit(request,'MESSAGE_RETRY',person,new={'message_id':str(obj.pk),'reason':reason})
        return Response({'status':obj.status})
    raise ValidationError('Unknown communication action.')
