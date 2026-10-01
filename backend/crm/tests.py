from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import Activity, AuditEvent, Branch, ContactMethod, FollowUp, Person, Source, StaffProfile
from .permissions import MATRIX
from .services import normalize_phone, next_reference

class CrmTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name='Kathmandu',code='KTM')
        self.other_branch = Branch.objects.create(name='Pokhara',code='PKR')
        self.owner = self.user('owner','COUNSELOR',self.branch)
        self.other = self.user('other','COUNSELOR',self.branch)
        self.manager = self.user('manager','MANAGER',self.branch)
        self.source = Source.objects.create(name='Walk-in')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
    def user(self,name,role,branch):
        user=User.objects.create_user(username=name,password='test-password-123')
        StaffProfile.objects.create(user=user,role=role,branch=branch)
        return user
    def payload(self,**kwargs):
        return {'full_name':'सुमन श्रेष्ठ','phone':'9801234567','email':'suman@example.com','branch_id':self.branch.pk,'owner_id':self.owner.pk,'source_id':self.source.pk,'consent':True,**kwargs}
    def create(self,**kwargs):
        return self.client.post('/api/v1/people/',self.payload(**kwargs),format='json')
    def test_nepal_phone_variants(self):
        for phone in ['9801234567','09801234567','+977 980-123-4567']:
            self.assertEqual(normalize_phone(phone),'+9779801234567')
    def test_create_unicode_consent_and_audit(self):
        response=self.create()
        self.assertEqual(response.status_code,201,response.data)
        person=Person.objects.get()
        self.assertEqual(person.full_name,'सुमन श्रेष्ठ')
        self.assertTrue(person.ref.startswith('BE-'))
        self.assertEqual(ContactMethod.objects.get(type='PHONE').normalized_value,'+9779801234567')
        self.assertEqual(person.consentrecord_set.count(),1)
        self.assertEqual(AuditEvent.objects.count(),1)
    def test_duplicate_warning_no_silent_merge(self):
        self.assertEqual(self.create().status_code,201)
        self.assertEqual(self.create().status_code,409)
        self.assertEqual(Person.objects.count(),1)
        response=self.create(duplicate_reason='Shared family contact; different person')
        self.assertEqual(response.status_code,201)
        self.assertEqual(Person.objects.count(),2)
    def test_other_owner_duplicates_are_masked_and_cannot_override(self):
        self.client.force_authenticate(self.manager)
        self.create(owner_id=self.other.pk)
        self.client.force_authenticate(self.owner)
        response=self.create(duplicate_reason='Ignore')
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.data['code'],'duplicate_access_required')
        self.assertNotIn('name',response.data['matches'][0])
        self.assertNotIn('ref',response.data['matches'][0])
    def test_scope_prevents_direct_api_bypass(self):
        person=self.create().data
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/v1/people/').data['count'],0)
        for path in ['', 'convert/', 'activities/', 'followups/']:
            url=f'/api/v1/people/{person["id"]}/{path}'
            response=self.client.get(url) if not path else self.client.post(url,{},format='json')
            if not path:
                self.assertEqual(response.status_code,200);self.assertTrue(response.data['masked']);self.assertNotIn('person',response.data);self.assertNotIn('full_name',response.data['record'])
            else:self.assertEqual(response.status_code,404)
        self.assertEqual(self.create(owner_id=self.owner.pk,phone='9801234568').status_code,403)
    def test_manager_branch_scope(self):
        outsider=self.user('outsider','COUNSELOR',self.other_branch)
        Person.objects.create(ref=next_reference(),full_name='Outside',owner=outsider,branch=self.other_branch,source=self.source,created_by=outsider)
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.get('/api/v1/people/').data['count'],0)
        self.assertEqual(self.create(owner_id=outsider.pk,branch_id=self.other_branch.pk).status_code,403)
    def test_convert_preserves_identity_and_history(self):
        person=self.create().data
        url=f'/api/v1/people/{person["id"]}/convert/'
        response=self.client.post(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['id'],person['id'])
        self.assertEqual(response.data['stage'],'STUDENT')
        self.assertEqual(Person.objects.count(),1)
        self.assertEqual(Activity.objects.count(),2)
        self.assertEqual(self.client.post(url).status_code,400)
    def test_next_action_recomputed_on_completion(self):
        person=self.create().data
        due1=timezone.now()+timedelta(hours=1)
        due2=timezone.now()+timedelta(hours=2)
        ids=[]
        for due in [due2,due1]:
            response=self.client.post(f'/api/v1/people/{person["id"]}/followups/',{'subject':'Call','method':'CALL','due_at':due.isoformat()},format='json')
            self.assertEqual(response.status_code,201,response.data)
            ids.append(response.data['id'])
        self.assertEqual(Person.objects.get().next_action_due_at,due1)
        self.assertEqual(self.client.post(f'/api/v1/followups/{ids[1]}/complete/',{'outcome':'Reached'},format='json').status_code,200)
        self.assertEqual(Person.objects.get().next_action_due_at,due2)
        self.client.post(f'/api/v1/followups/{ids[0]}/complete/',{'outcome':'Reached'},format='json')
        self.assertIsNone(Person.objects.get().next_action_due_at)
    def test_permission_configuration_role_pairs(self):
        for i,(role,permissions) in enumerate(MATRIX.items()):
            user=self.user('role_'+role,role,self.branch)
            self.client.force_authenticate(user)
            with self.subTest(role=role):
                self.assertEqual(self.client.get('/api/v1/people/').status_code,200 if 'view' in permissions else 403)
                response=self.create(phone=f'98012345{80+i:02}',email=f'{role.lower()}@example.com',owner_id=self.owner.pk)
                self.assertEqual(response.status_code,201 if role in ['ADMIN','MANAGER'] else 403)
    def test_auth_and_csrf_enforced(self):
        client=APIClient(enforce_csrf_checks=True)
        self.assertEqual(client.get('/api/v1/people/').status_code,403)
        self.assertEqual(client.post('/api/v1/auth/login/',{'username':'owner','password':'test-password-123'}).status_code,403)
        token=client.get('/api/v1/auth/session/').data['csrf_token']
        response=client.post('/api/v1/auth/login/',{'username':'owner','password':'test-password-123'},HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(client.get('/api/v1/people/').status_code,200)
    def test_privileged_login_requires_mfa(self):
        self.user('admin','ADMIN',self.branch)
        client=APIClient(enforce_csrf_checks=True)
        token=client.get('/api/v1/auth/session/').data['csrf_token']
        response=client.post('/api/v1/auth/login/',{'username':'admin','password':'test-password-123'},HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.data['mfa_required'])
        self.assertEqual(client.get('/api/v1/people/').status_code,403)
    def test_invalid_data_cannot_write(self):
        for fields in [{'phone':'bad'},{'consent':False},{'full_name':''}]:
            self.assertEqual(self.create(**fields).status_code,400)
        self.assertEqual(Person.objects.count(),0)
