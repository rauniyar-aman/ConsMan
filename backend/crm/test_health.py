from unittest.mock import patch
from django.db import OperationalError
from django.test import TestCase


class DatabaseHealthTests(TestCase):
    def test_ready_database(self):
        self.assertEqual(self.client.get('/api/v1/health/').status_code, 200)

    def test_unavailable_database_is_not_healthy(self):
        with patch('crm.api.connection.cursor', side_effect=OperationalError('private connection detail')):
            response = self.client.get('/api/v1/health/')
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, 'private connection detail', status_code=503)
