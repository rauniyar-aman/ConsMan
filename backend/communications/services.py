import re
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.core import signing
from rest_framework.exceptions import ValidationError
from .models import ChannelConsent, OutboundMessage


def render(template, person, context=None):
    values = {'student_name': person.full_name, 'person_reference': person.ref, 'branch_name': person.branch.name}
    values.update({key:str((context or {}).get(key,'')) for key in ['due_at','event_title','application_reference','application_state']})
    def replace(match):
        key = match.group(1)
        if key not in values:
            raise ValidationError('Unsupported template field: ' + key)
        return values[key]
    subject,body=tuple(re.sub(r'\{\{\s*([a-z_]+)\s*\}\}', replace, value) for value in [template.subject, template.body])
    if template.channel=='EMAIL':
        token=signing.dumps({'person':str(person.pk),'channel':'EMAIL'},salt='communication-opt-out')
        body+='\n\nUnsubscribe: '+settings.PUBLIC_FORM_ORIGIN.rstrip('/')+'/api/v1/communications/opt-out/'+token+'/'
    else:body+='\n\nReply STOP to opt out.'
    return subject.replace('\r',' ').replace('\n',' '),body


def consent_for(person, channel):
    return ChannelConsent.objects.filter(person=person, channel=channel).order_by('-id').first()


def display_body(body):
    return re.sub(r'https?://[^\s]+/api/v1/communications/opt-out/[^\s]+','(unsubscribe link sent to recipient)',body)


@transaction.atomic
def queue(person, contact, template, actor, scheduled_at=None, context=None):
    from crm.models import Person
    Person.objects.select_for_update().get(pk=person.pk)
    consent = consent_for(person, template.channel)
    if not consent or not consent.allowed:
        raise ValidationError('Explicit current consent is required for this channel.')
    expected = 'EMAIL' if template.channel == 'EMAIL' else 'PHONE'
    if contact.person_id != person.pk or contact.type != expected or not contact.verified_at:
        raise ValidationError('Select a verified contact belonging to this person.')
    if person.archived_at or person.merged_into_id:
        raise ValidationError('Archived or merged people cannot receive messages.')
    subject, body = render(template, person, context)
    if not body.strip():
        raise ValidationError('A message body is required.')
    obj=OutboundMessage.objects.create(person=person, contact=contact, template=template, consent=consent, channel=template.channel, recipient=contact.normalized_value, subject=subject, body=body, scheduled_at=scheduled_at or timezone.now(), created_by=actor)
    from .models import MessageEvent
    MessageEvent.objects.create(message=obj,status='QUEUED')
    return obj


def eligible(message):
    consent = consent_for(message.person, message.channel)
    return bool(consent and consent.allowed and not message.person.archived_at and not message.person.merged_into_id and message.contact.verified_at and message.contact.person_id == message.person_id and message.contact.normalized_value == message.recipient)
