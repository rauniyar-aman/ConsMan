import re
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import ChannelConsent, OutboundMessage


def render(template, person):
    values = {'student_name': person.full_name, 'person_reference': person.ref, 'branch_name': person.branch.name}
    def replace(match):
        key = match.group(1)
        if key not in values:
            raise ValidationError('Unsupported template field: ' + key)
        return values[key]
    return tuple(re.sub(r'\{\{\s*([a-z_]+)\s*\}\}', replace, value) for value in [template.subject, template.body])


def consent_for(person, channel):
    return ChannelConsent.objects.filter(person=person, channel=channel).order_by('-id').first()


@transaction.atomic
def queue(person, contact, template, actor, scheduled_at=None):
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
    subject, body = render(template, person)
    if not body.strip():
        raise ValidationError('A message body is required.')
    return OutboundMessage.objects.create(person=person, contact=contact, template=template, consent=consent, channel=template.channel, recipient=contact.normalized_value, subject=subject, body=body, scheduled_at=scheduled_at or timezone.now(), created_by=actor)


def eligible(message):
    consent = consent_for(message.person, message.channel)
    return bool(consent and consent.allowed and not message.person.archived_at and not message.person.merged_into_id and message.contact.verified_at and message.contact.person_id == message.person_id and message.contact.normalized_value == message.recipient)
