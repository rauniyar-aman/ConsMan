import phonenumbers
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import Activity, AuditEvent, ContactMethod, Person, ReferenceCounter, Task, DuplicateCandidate, Notification, StaffProfile, OwnershipHistory, AssignmentRule

def normalize_phone(value):
    try:
        phone = phonenumbers.parse(value, 'NP')
        if not phonenumbers.is_valid_number(phone):
            raise ValueError()
        return phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)
    except (phonenumbers.NumberParseException, ValueError):
        raise ValidationError({'phone': 'Enter a valid phone number, e.g. +977 9801234567.'})

def duplicates(phone, email=''):
    query = Q(type='PHONE', normalized_value=phone)
    if email:
        query |= Q(type='EMAIL', normalized_value=email)
    return Person.objects.filter(archived_at__isnull=True, contacts__in=ContactMethod.objects.filter(query)).distinct()

def audit(request, action, person, old=None, new=None):
    AuditEvent.objects.create(actor=request.user if request.user.is_authenticated else None, action=action, object_id=str(person.id), branch=getattr(person,'branch',None), old=old or {}, new=new or {}, request_id=request.request_id)

@transaction.atomic
def next_reference():
    year = timezone.localtime().year
    counter, _ = ReferenceCounter.objects.get_or_create(year=year)
    ReferenceCounter.objects.filter(pk=counter.pk).update(value=F('value') + 1)
    counter.refresh_from_db()
    return f'BE-{year}-{counter.value:06d}'

def activity(request, person, subject, notes='', kind='SYSTEM'):
    item = Activity.objects.create(person=person, subject=subject, notes=notes, type=kind, performed_by=request.user if request.user.is_authenticated else None, channel=kind if kind in ['CALL','WHATSAPP','SMS','MESSENGER','EMAIL','MEETING'] else 'SYSTEM')
    Person.objects.filter(pk=person.pk).update(last_activity_at=item.performed_at,updated_at=timezone.now())
    if kind in ['CALL','WHATSAPP','SMS','MESSENGER','EMAIL','MEETING']:
        from .calendar import satisfy
        satisfy(person,['FIRST_CONTACT','HOT_CONTACT'])
    return item

def refresh_next_action(person):
    followup = person.followups.filter(completed_at__isnull=True,status__in=['OPEN','IN_PROGRESS']).order_by('due_at').first()
    task = person.tasks.filter(status__in=['OPEN','IN_PROGRESS']).order_by('due_at').first()
    items=[('FOLLOWUP',followup),('TASK',task)]
    kind,next_item=min((item for item in items if item[1]),key=lambda item:item[1].due_at,default=('',None))
    person.next_action_due_at = next_item.due_at if next_item else None
    person.next_action_type=kind
    person.next_action_owner_id=next_item.owner_id if next_item else None
    person.save(update_fields=['next_action_due_at','next_action_type','next_action_owner','updated_at'])

def normalize_name(name):
    import re
    from unidecode import unidecode
    value=re.sub(r'^(mr|mrs|ms|miss|dr)\.?\s+','', ' '.join(name.casefold().split()))
    tokens=re.findall(r'\w+',unidecode(value).casefold())
    return ' '.join(sorted(tokens))[:160]

def duplicate_signals(phone='',email='',name='',dob=None,exclude=None,country=''):
    query=Q(pk__in=[])
    if phone: query|=Q(contacts__normalized_value=phone,contacts__type='PHONE')
    if email: query|=Q(contacts__normalized_value=email,contacts__type='EMAIL')
    normalized=normalize_name(name) if name else ''
    if normalized:
        from difflib import SequenceMatcher
        # Keep fuzzy suggestions bounded; exact identity/contact signals are never capped.
        tokens=sorted(set(normalized.split()),key=len,reverse=True)[:2]
        fuzzy=Q(pk__in=[])
        for token in tokens:
            if len(token)>=3:fuzzy|=Q(normalized_name__contains=token)
        ids=list(Person.objects.filter(fuzzy,archived_at__isnull=True).order_by('pk').values_list('pk',flat=True)[:200])
        query|=Q(normalized_name=normalized)|Q(full_name__iexact=name.strip())|Q(pk__in=ids)
    matches=Person.objects.filter(query,archived_at__isnull=True).exclude(pk=exclude).distinct().prefetch_related('contacts').select_related('owner','branch')
    output=[]
    for person in matches:
        phone_matches=[c for c in person.contacts.all() if c.type=='PHONE' and c.normalized_value==phone]
        email_match=any(c.type=='EMAIL' and c.normalized_value==email for c in person.contacts.all()) if email else False
        name_match=bool(normalized) and normalize_name(person.full_name)==normalized
        similar=bool(normalized) and not name_match and SequenceMatcher(None,normalized,normalize_name(person.full_name)).ratio()>=0.85
        signals=[]
        if phone_matches:signals.append('SHARED_PHONE' if all(c.is_shared for c in phone_matches) else 'PHONE')
        if email_match:signals.append('EMAIL')
        if name_match:signals.append('NAME')
        elif similar:signals.append('NAME_SIMILARITY')
        if not signals:continue
        if name_match and dob and str(person.dob)==str(dob):signals.append('DOB')
        if (name_match or similar) and country and person.preferred_country.casefold()==country.casefold():signals.append('COUNTRY')
        confidence='EXACT' if 'PHONE' in signals and 'EMAIL' in signals else 'HIGH' if ('PHONE' in signals or 'EMAIL' in signals or 'DOB' in signals) else 'MEDIUM' if ('SHARED_PHONE' in signals or 'COUNTRY' in signals) else 'LOW'
        output.append({'person':person,'confidence':confidence,'signals':signals})
    return output

def queue_candidates(person, matches):
    for match in matches:
        if match['confidence']=='LOW':continue
        a,b=sorted([person.id,match['person'].id],key=str)
        candidate,created=DuplicateCandidate.objects.get_or_create(person_a_id=a,person_b_id=b,defaults={'confidence':match['confidence'],'signals':match['signals']})
        if created:
            notify_managers(person.branch,'DUPLICATE','Duplicate review required',person, f'duplicate:{candidate.pk}')

def notify(user,kind,title,person=None,key=None,message=''):
    if not user:return
    if key:
        return Notification.objects.get_or_create(dedupe_key=f'{user.pk}:{key}',defaults={'recipient':user,'type':kind,'title':title,'person':person,'message':message})[0]
    return Notification.objects.create(recipient=user,type=kind,title=title,person=person,message=message)

def notify_managers(branch,kind,title,person=None,key=None,message=''):
    for staff in StaffProfile.objects.filter(Q(role='ADMIN')|Q(branch=branch,role='MANAGER'),user__is_active=True).select_related('user'):
        notify(staff.user,kind,title,person,key,message)

def assign_owner(request,person,owner,reason):
    from .calendar import satisfy
    previous=person.owner
    person.owner=owner;person.save(update_fields=['owner','updated_at'])
    OwnershipHistory.objects.create(person=person,from_user=previous,to_user=owner,reason=reason,changed_by=request.user if request.user.is_authenticated else None,request_id=request.request_id)
    person.followups.filter(completed_at__isnull=True,status__in=['OPEN','IN_PROGRESS']).filter(Q(owner=previous)|Q(owner__isnull=True)).update(owner=owner)
    refresh_next_action(person)
    activity(request,person,f'Assigned to {owner.get_full_name() if owner else "manager queue"}',reason,'ASSIGNMENT')
    audit(request,'OWNER_CHANGED',person,{'owner_id':previous.pk if previous else None},{'owner_id':owner.pk if owner else None,'reason':reason})
    if owner:
        notify(owner,'ASSIGNMENT','A person was assigned to you',person)
        satisfy(person,['ASSIGNMENT'])

def automatic_owner(branch):
    rule,_=AssignmentRule.objects.get_or_create(branch=branch)
    rule=AssignmentRule.objects.select_for_update().get(pk=rule.pk)
    if rule.mode!='ROUND_ROBIN':return None
    eligible=[]
    for staff in StaffProfile.objects.filter(branch=branch,role='COUNSELOR',user__is_active=True).select_related('user').order_by('user_id'):
        unavailable=staff.availability=='INACTIVE' or (staff.availability=='ON_LEAVE' and (not staff.leave_until or staff.leave_until>=timezone.localdate()))
        if unavailable:continue
        count=Person.objects.filter(owner=staff.user,stage='LEAD',archived_at__isnull=True).exclude(lead_status__in=['LOST','ON_HOLD']).count()
        if count<staff.max_open_leads:eligible.append(staff.user)
    owner=next((u for u in eligible if u.pk>rule.last_owner_id),eligible[0] if eligible else None)
    if owner:rule.last_owner_id=owner.pk;rule.save(update_fields=['last_owner_id'])
    return owner
