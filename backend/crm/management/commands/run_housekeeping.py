from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from crm.models import StaffProfile,IdempotencyRecord
from crm.operations import materialize_notifications
from intake.models import IntakeSubmission,RateBucket,RateEvent,OtpChallenge
from intake.services import policy

class Command(BaseCommand):
    help='Run scheduled in-app escalation, retention, and verification cleanup.'
    def handle(self,*args,**options):
        now=timezone.now()
        # Clear raw personal payloads but preserve aggregate records and append-only audit evidence.
        expired=IntakeSubmission.objects.filter(status__in=['UNVERIFIED','DISCARDED'],purge_after__lte=now)
        for submission in expired:
            submission.challenges.update(status='EXPIRED',phone_e164='',salt='',code_hash='')
            submission.payload={};submission.phone_e164='';submission.ip_hash='';submission.resume_token_hash='';submission.status='PURGED';submission.save()
        resolved=IntakeSubmission.objects.filter(status='PROCESSED',updated_at__lt=now-timedelta(days=policy('resolved_intake_retention_days')))
        OtpChallenge.objects.filter(submission__in=resolved).update(phone_e164='',salt='',code_hash='')
        resolved.update(payload={},phone_e164='',ip_hash='',resume_token_hash='')
        RateEvent.objects.filter(at__lt=now-timedelta(hours=24)).delete()
        RateBucket.objects.filter(expires_at__lt=now).delete()
        from crm.models import Person,SystemPolicy
        from crm.services import notify_managers
        months=SystemPolicy.objects.filter(key='closed_lead_retention_months').values_list('value',flat=True).first() or 36
        for person in Person.objects.filter(lead_status='LOST',updated_at__lt=now-timedelta(days=months*30),archived_at__isnull=True).select_related('branch'):
            notify_managers(person.branch,'RETENTION_REVIEW','Closed lead needs retention review',person,key=f'retention:{person.pk}')
        for staff in StaffProfile.objects.filter(role__in=['ADMIN','MANAGER','COUNSELOR','DOCS'],user__is_active=True).select_related('user'):materialize_notifications(staff.user)
        self.stdout.write(self.style.SUCCESS('Retention and operational escalations processed.'))
