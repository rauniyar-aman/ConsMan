from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import BusinessCalendar, SlaRule, SlaTimer

def business_due(start, hours, calendar):
    zone=ZoneInfo(calendar.timezone)
    current=start.astimezone(zone)
    remaining=float(hours)*3600
    if not calendar.working_days or calendar.open_time>=calendar.close_time:
        raise ValidationError('Business calendar needs working days and a valid opening interval.')
    holidays=set(calendar.holidays)
    for _ in range(3660):
        day=current.date()
        opening=datetime.combine(day,calendar.open_time,tzinfo=zone)
        closing=datetime.combine(day,calendar.close_time,tzinfo=zone)
        if day.weekday() not in calendar.working_days or str(day) in holidays or current>=closing:
            current=datetime.combine(day+timedelta(days=1),calendar.open_time,tzinfo=zone)
            continue
        current=max(current,opening)
        available=(closing-current).total_seconds()
        if remaining<=available:
            return current+timedelta(seconds=remaining)
        remaining-=available
        current=datetime.combine(day+timedelta(days=1),calendar.open_time,tzinfo=zone)
    raise ValidationError('No SLA due date could be computed within the calendar horizon.')

def start_sla(person, trigger, access_request=None, start=None):
    rule=SlaRule.objects.filter(trigger=trigger,active=True).first()
    if not rule:
        return None
    calendar,_=BusinessCalendar.objects.get_or_create(branch=person.branch)
    started=start or timezone.now()
    return SlaTimer.objects.create(person=person,access_request=access_request,branch=person.branch,rule=rule,started_at=started,due_at=business_due(started,rule.target_business_hours,calendar))

def recompute_calendar(calendar):
    for timer in SlaTimer.objects.filter(branch=calendar.branch,satisfied_at__isnull=True).select_related('rule'):
        timer.due_at=business_due(timer.started_at,timer.rule.target_business_hours,calendar)
        timer.save(update_fields=['due_at'])

def satisfy(person, triggers):
    person.sla_timers.filter(rule__trigger__in=triggers,satisfied_at__isnull=True).update(satisfied_at=timezone.now())
