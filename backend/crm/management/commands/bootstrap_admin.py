import os, uuid
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from crm.models import Branch, StaffProfile, AuditEvent


class Command(BaseCommand):
    help = 'Provision the initial administrator once, using private environment values.'

    @transaction.atomic
    def handle(self, *args, **options):
        if StaffProfile.objects.filter(role='ADMIN').exists():
            raise CommandError('An administrator already exists; use Settings for staff management.')
        username = os.getenv('BOOTSTRAP_ADMIN_USERNAME', '').strip()
        password = os.getenv('BOOTSTRAP_ADMIN_PASSWORD', '')
        branch = Branch.objects.filter(code=os.getenv('BOOTSTRAP_ADMIN_BRANCH', 'KTM')).first()
        if not username or not password or not branch:
            raise CommandError('Set BOOTSTRAP_ADMIN_USERNAME, BOOTSTRAP_ADMIN_PASSWORD and a valid BOOTSTRAP_ADMIN_BRANCH.')
        if User.objects.filter(username=username).exists():
            raise CommandError('Choose an unused administrator username.')
        user = User(username=username)
        try:
            validate_password(password, user)
        except ValidationError:
            raise CommandError('Administrator password does not meet password policy.')
        user.set_password(password)
        user.save()
        StaffProfile.objects.create(user=user, role='ADMIN', branch=branch)
        AuditEvent.objects.create(actor=user, action='INITIAL_ADMIN_PROVISIONED', branch=branch,
                                  object_id=str(user.pk), request_id=uuid.uuid4())
        self.stdout.write(self.style.SUCCESS('Initial administrator provisioned; enroll MFA at first sign-in.'))
