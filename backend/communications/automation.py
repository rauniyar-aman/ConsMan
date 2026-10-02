from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from crm.models import FollowUp,Task,Notification
from admissions.models import Deadline,ApplicationEvent
from .models import AutomationRule,AutomationRun
from .services import queue,consent_for


def candidates(rule):
    now=timezone.now();until=now+timedelta(minutes=rule.advance_minutes)
    if rule.trigger=='FOLLOWUP_DUE':
        for row in FollowUp.objects.filter(person__branch=rule.branch,status__in=['OPEN','IN_PROGRESS'],completed_at__isnull=True,due_at__gte=rule.starts_at,due_at__lte=until).select_related('person__branch').iterator(chunk_size=500):yield row.person,row.owner,f'followup:{row.pk}:{row.due_at.isoformat()}',row.subject,{'due_at':timezone.localtime(row.due_at).isoformat(),'event_title':row.subject}
    elif rule.trigger=='TASK_DUE':
        for row in Task.objects.filter(person__branch=rule.branch,status__in=['OPEN','IN_PROGRESS'],completed_at__isnull=True,due_at__gte=rule.starts_at,due_at__lte=until).select_related('person__branch').iterator(chunk_size=500):yield row.person,row.owner,f'task:{row.pk}:{row.due_at.isoformat()}',row.title,{'due_at':timezone.localtime(row.due_at).isoformat(),'event_title':row.title}
    elif rule.trigger=='APPLICATION_DEADLINE':
        for row in Deadline.objects.filter(person__branch=rule.branch,status='OPEN',due_at__gte=rule.starts_at,due_at__lte=until).select_related('person__branch','application').iterator(chunk_size=500):
            if not row.completed_at:yield row.person,row.application.owner if row.application else row.person.owner,f'deadline:{row.pk}:{row.due_at.isoformat()}',row.title,{'due_at':timezone.localtime(row.due_at).isoformat(),'event_title':row.title,'application_reference':row.application.ref if row.application else ''}
    else:
        for row in ApplicationEvent.objects.filter(application__person__branch=rule.branch,created_at__gte=rule.starts_at,type__in=['STATE_CHANGED','OFFER_ACCEPTED','VISA_STATE_CHANGED','ENROLLMENT_CONFIRMED']).select_related('application__person__branch').iterator(chunk_size=500):yield row.application.person,row.application.owner,f'application:{row.pk}',f'Application {row.application.ref} status updated',{'event_title':'Application status updated','application_reference':row.application.ref,'application_state':row.new.get('state',row.application.state)}


def run_rule(pk):
    with transaction.atomic():
        rule=AutomationRule.objects.select_for_update(of=('self',)).select_related('template','created_by').get(pk=pk)
        if not rule.enabled or not rule.created_by.is_active:return
        seen=set(AutomationRun.objects.filter(rule=rule).values_list('event_key',flat=True))
        for person,owner,key,title,context in candidates(rule):
            if key in seen:continue
            if person.archived_at or person.merged_into_id:continue
            run,created=AutomationRun.objects.get_or_create(rule=rule,event_key=key,defaults={'outcome':'PENDING'})
            if not created:continue
            outcome=[]
            if rule.staff_notifications and owner and owner.is_active:
                Notification.objects.get_or_create(dedupe_key=f'communication:{run.pk}',defaults={'recipient':owner,'type':'AUTOMATION','title':title[:160],'person':person});outcome.append('STAFF_NOTIFIED')
            if rule.student_messages and rule.template:
                consent=consent_for(person,rule.template.channel)
                kind='EMAIL' if rule.template.channel=='EMAIL' else 'PHONE'
                contact=person.contacts.filter(type=kind,verified_at__isnull=False).order_by('-is_primary','pk').first()
                if consent and consent.allowed and contact:
                    obj=queue(person,contact,rule.template,rule.created_by,context=context);obj.dedupe_key=f'automation:{run.pk}';obj.save(update_fields=['dedupe_key']);outcome.append('STUDENT_QUEUED')
                else:outcome.append('CONSENT_OR_CONTACT_MISSING')
            run.outcome=','.join(outcome)[:60] or 'NO_RECIPIENT';run.save(update_fields=['outcome'])
