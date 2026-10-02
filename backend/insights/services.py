from django.utils import timezone
from admissions.models import CourseOffering, StudentPreference
from admissions.services import matching


def completeness(person):
    results = []
    for doc in person.admission_documents.all().order_by('title', 'pk'):
        expired = bool(doc.expires_at and doc.expires_at <= timezone.now())
        complete = doc.status in ['WAIVED', 'NOT_APPLICABLE'] or (doc.status == 'VERIFIED' and not expired)
        results.append({'id': str(doc.pk), 'title': doc.title, 'type': doc.type,
            'application_id': str(doc.application_id) if doc.application_id else None,
            'required': doc.required, 'status': 'EXPIRED' if expired and doc.status == 'VERIFIED' else doc.status,
            'complete': complete, 'version': doc.version,
            'evidence': f'Document {doc.pk}: {doc.status}, version {doc.version}'})
    return results


def priority(person):
    now = timezone.now()
    reasons = []
    for followup in person.followups.filter(completed_at__isnull=True).exclude(status='CANCELLED').filter(due_at__lt=now).order_by('due_at'):
        reasons.append({'kind': 'OVERDUE_FOLLOWUP', 'id': str(followup.pk), 'title': followup.subject, 'at': followup.due_at.isoformat(), 'weight': 30})
    for deadline in person.admission_deadlines.filter(status='OPEN', due_at__lte=now+timezone.timedelta(days=7)).order_by('due_at'):
        reasons.append({'kind': 'DEADLINE', 'id': str(deadline.pk), 'title': deadline.title, 'at': deadline.due_at.isoformat(), 'weight': 40 if deadline.due_at < now else 20})
    for blocker in person.admission_blockers.filter(resolved_at__isnull=True):
        reasons.append({'kind': 'BLOCKER', 'id': str(blocker.pk), 'title': blocker.type, 'weight': 25})
    missing = [d for d in completeness(person) if d['required'] and not d['complete']]
    for doc in missing:
        reasons.append({'kind': 'DOCUMENT', 'id': doc['id'], 'title': doc['title'], 'weight': 10})
    if person.stage == 'LEAD' and not person.next_action_due_at and person.lead_status not in ['LOST', 'ON_HOLD']:
        reasons.append({'kind': 'NO_NEXT_ACTION', 'id': str(person.pk), 'title': 'No next action scheduled', 'weight': 15})
    score = min(100, sum(r['weight'] for r in reasons))
    return {'score': score, 'health': 'BLOCKED' if any(r['kind'] == 'BLOCKER' for r in reasons) else 'ATTENTION_REQUIRED' if reasons else 'ON_TRACK', 'reasons': reasons}


def evidence(person):
    apps = list(person.applications.order_by('created_at').values('id', 'ref', 'state'))
    documents = completeness(person)
    rank = priority(person)
    timeline = []
    for item in person.activities.order_by('-performed_at')[:30]:
        timeline.append({'kind': 'ACTIVITY', 'id': str(item.pk), 'title': item.subject, 'at': item.performed_at.isoformat()})
    for app in person.applications.all():
        for item in app.events.order_by('-created_at')[:20]:
            timeline.append({'kind': 'APPLICATION_EVENT', 'id': str(item.pk), 'title': f'{app.ref}: {item.type}', 'at': item.created_at.isoformat()})
        for case in app.visa_cases.all():
            for item in case.events.order_by('-created_at')[:20]:
                timeline.append({'kind': 'VISA_EVENT', 'id': str(item.pk), 'title': f'{case.ref}: {item.type}', 'at': item.created_at.isoformat()})
    timeline = sorted(timeline, key=lambda e:e['at'], reverse=True)[:50]
    pref = StudentPreference.objects.filter(person=person).first()
    offerings = CourseOffering.objects.filter(active=True, course__active=True, campus__active=True,
        campus__university__active=True, intake__active=True).exclude(availability='CLOSED').select_related('course', 'campus__university', 'intake').prefetch_related('scholarships')
    if pref and pref.countries: offerings = offerings.filter(campus__university__country__in=pref.countries)
    elif person.preferred_countries: offerings = offerings.filter(campus__university__country__in=person.preferred_countries)
    offers_count = offerings.count()
    selected = list(offerings.order_by('pk')[:200])
    matches = matching(person, pref, selected)
    custom = {o.pk: bool(o.course.requirements or o.course.english_requirements or o.requirements) for o in selected}
    for match in matches:
        if custom[match['offering_id']]:
            match['missing_information'].append('Review institution free-text requirements; structured checks cannot establish full eligibility')
            if match['result'] == 'MATCHED': match['result'] = 'REVIEW_REQUIRED'
    incomplete = [d for d in documents if d['required'] and not d['complete']]
    summary = f'{person.ref}: {person.stage}, {person.lead_status}. {len(apps)} application(s); {len(incomplete)} required document(s) incomplete. Journey: {rank["health"].replace("_", " ").lower()}.'
    draft = 'Hello ' + person.full_name + ',\n\n'
    if incomplete:
        draft += 'Please provide the documents requested by our office: ' + ', '.join(d['title'] for d in incomplete[:10]) + '.\n'
    open_actions=list(person.student_requests.filter(status__in=['OPEN','NEEDS_CHANGES']).values('id','title'))
    if open_actions:draft+='Please respond to your office requests: '+', '.join(a['title'] for a in open_actions[:10])+'.\n'
    if not incomplete and not open_actions: draft += 'Please contact our office for your current application updates or if you need help.\n'
    draft += '\nThank you,\nThe Blessing Edu'
    return {'engine': 'Local evidence rules, version 1', 'generated_at': timezone.now().isoformat(),
        'summary': summary, 'applications': [{**a, 'id': str(a['id'])} for a in apps],
        'documents': documents, 'priority': rank, 'timeline': timeline, 'course_matches': matches,
        'course_count': offers_count, 'course_limit': 200, 'draft': draft,
        'draft_evidence': [d['id'] for d in incomplete[:10]],
        'review_notice': 'Confirm the recorded evidence and institution requirements. Suggestions do not establish eligibility, admission, payment or visa outcomes.'}
