from io import StringIO
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from crm.models import Branch, StaffProfile, AuditEvent


class BootstrapAdministratorTests(TestCase):
    def test_initial_account_and_refusal_to_replace_it(self):
        Branch.objects.create(name='Kathmandu', code='KTM')
        output = StringIO()
        with patch.dict('os.environ', {'BOOTSTRAP_ADMIN_USERNAME': 'initial-owner',
                                      'BOOTSTRAP_ADMIN_PASSWORD': 'Strong-private-fixture-935!',
                                      'BOOTSTRAP_ADMIN_BRANCH': 'KTM'}):
            call_command('bootstrap_admin', stdout=output)
            with self.assertRaises(CommandError):
                call_command('bootstrap_admin', stdout=output)
        self.assertEqual(StaffProfile.objects.get().role, 'ADMIN')
        self.assertTrue(User.objects.get().check_password('Strong-private-fixture-935!'))
        self.assertTrue(AuditEvent.objects.filter(action='INITIAL_ADMIN_PROVISIONED').exists())
        self.assertNotIn('Strong-private-fixture-935!', output.getvalue())
