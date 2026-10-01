import os
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from crm.models import MfaDevice,AuditEvent

class Command(BaseCommand):
    help='Reviewed break-glass MFA reset. Requires trusted server access and a reason.'
    def add_arguments(self,parser):
        parser.add_argument('username')
        parser.add_argument('--reason',required=True)
    @transaction.atomic
    def handle(self,*args,**options):
        if os.getenv('ALLOW_MFA_RESET')!='true':raise CommandError('Explicitly set ALLOW_MFA_RESET=true in the trusted operator shell.')
        reason=options['reason'].strip()
        if not reason or len(reason)>500:raise CommandError('Provide a reviewed reason of 1–500 characters.')
        user=User.objects.filter(username=options['username'],is_active=True,staff__isnull=False).first()
        if not user:raise CommandError('Active staff account not found.')
        MfaDevice.objects.filter(user=user).delete()
        from django.contrib.sessions.models import Session
        for session in Session.objects.filter(expire_date__gt=__import__('django.utils.timezone',fromlist=['now']).now()):
            data=session.get_decoded()
            if str(data.get('_auth_user_id'))==str(user.pk) or data.get('mfa_pending')==user.pk:session.delete()
        AuditEvent.objects.create(action='MFA_OPERATOR_RESET',branch=user.staff.branch,object_id=str(user.pk),request_id=__import__('uuid').uuid4(),new={'reason':reason,'operator':os.getenv('USERNAME',os.getenv('USER','server-operator'))})
        self.stdout.write('MFA reset audited; sessions revoked. User must enroll after password verification.')
