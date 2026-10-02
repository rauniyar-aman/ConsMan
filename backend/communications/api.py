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
from .services import queue, render


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
    lock, _ = JourneyCounter.objects.get_or_create(kind='MSG_TPL', year=0)
    JourneyCounter.objects.select_for_update().get(pk=lock.pk)
    version = (MessageTemplate.objects.filter(name=name, channel=code).aggregate(v=Max('version'))['v'] or 0) + 1
    obj = MessageTemplate(name=name, channel=code, version=version, body=body, subject=subject, created_by=request.user)
    # Check placeholders without requiring a real person's data.
    import re
    fields = re.findall(r'\{\{\s*([a-z_]+)\s*\}\}', body + subject)
    if any(f not in ['student_name', 'person_reference', 'branch_name'] for f in fields):
        raise ValidationError('Use student_name, person_reference or branch_name placeholders.')
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
        return Response({'consents': list(ChannelConsent.objects.filter(person=person).order_by('-id').values('id', 'channel', 'allowed', 'evidence', 'created_at')[:100]), 'messages': list(OutboundMessage.objects.filter(person=person).order_by('-created_at').values('id', 'channel', 'recipient', 'subject', 'body', 'status', 'scheduled_at', 'created_at')[:100])})
    Person.objects.select_for_update().get(pk=person.pk)
    data = request.data
    if data.get('action') == 'consent':
        code = channel(data)
        allowed = serializers.BooleanField().run_validation(data.get('allowed'))
        evidence = serializers.CharField(max_length=500).run_validation(data.get('evidence'))
        obj = ChannelConsent.objects.create(person=person, channel=code, allowed=allowed, evidence=evidence, recorded_by=request.user)
        if not allowed:
            OutboundMessage.objects.filter(person=person, channel=code, status='QUEUED').update(status='CANCELLED')
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
        obj.status = 'CANCELLED'
        obj.save(update_fields=['status'])
        audit(request, 'MESSAGE_CANCELLED', person, new={'message_id': str(obj.pk)})
        return Response({'status': obj.status})
    raise ValidationError('Unknown communication action.')
