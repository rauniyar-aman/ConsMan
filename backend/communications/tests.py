from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from crm.models import Branch, Person, Source, StaffProfile, ContactMethod
from .models import MessageTemplate, OutboundMessage
from .services import eligible


class CommunicationTests(TestCase):
    def setUp(self):
        self.branch=Branch.objects.create(name='Office',code='MSG')
        self.admin=User.objects.create_user(username='admin')
        StaffProfile.objects.create(user=self.admin,role='ADMIN',branch=self.branch)
        self.other=User.objects.create_user(username='counselor')
        StaffProfile.objects.create(user=self.other,role='COUNSELOR',branch=self.branch)
        self.person=Person.objects.create(ref='BE-MSG',full_name='Test Student',branch=self.branch,owner=self.admin,source=Source.objects.create(name='Messaging'))
        self.contact=ContactMethod.objects.create(person=self.person,type='EMAIL',raw_value='student@example.test',normalized_value='student@example.test',verified_at=timezone.now())
        self.client=APIClient();self.client.force_authenticate(self.admin)
        self.path=f'/api/v1/communications/people/{self.person.pk}/'
    def post(self,data):return self.client.post(self.path,data,format='json')
    def template(self):
        response=self.client.post('/api/v1/communications/templates/',{'name':'Welcome','channel':'EMAIL','subject':'Welcome','body':'Hello {{student_name}} at {{branch_name}}'},format='json')
        self.assertEqual(response.status_code,201,response.data)
        return response.data['id']
    def test_consent_required_queue_snapshot_and_opt_out(self):
        template=self.template();data={'action':'queue','template_id':template,'contact_id':self.contact.pk}
        self.assertEqual(self.post(data).status_code,400)
        self.assertEqual(self.post({'action':'consent','channel':'EMAIL','allowed':True,'evidence':'Student signed consent'}).status_code,201)
        self.assertEqual(self.post(data).status_code,201)
        message=OutboundMessage.objects.get();self.assertEqual(message.body,'Hello Test Student at Office');self.assertTrue(eligible(message))
        self.post({'action':'consent','channel':'EMAIL','allowed':False,'evidence':'Student opted out'})
        message.refresh_from_db();self.assertEqual(message.status,'CANCELLED');self.assertFalse(eligible(message));self.assertEqual(self.post(data).status_code,400)
    def test_templates_version_and_permissions(self):
        self.template();self.template();self.assertEqual(list(MessageTemplate.objects.values_list('version',flat=True)),[1,2])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.path).status_code,404)
        self.assertEqual(self.client.post('/api/v1/communications/templates/',{'name':'Unauthorized'},format='json').status_code,403)
    def test_contacts_and_rendering_validation(self):
        template=self.template();self.post({'action':'consent','channel':'EMAIL','allowed':True,'evidence':'Written consent'})
        self.contact.verified_at=None;self.contact.save()
        self.assertEqual(self.post({'action':'queue','template_id':template,'contact_id':self.contact.pk}).status_code,400)
        self.assertEqual(self.client.post('/api/v1/communications/templates/',{'name':'Bad','channel':'EMAIL','body':'{{unknown_field}}'},format='json').status_code,400)
