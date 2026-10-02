import hashlib,io
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction,DatabaseError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from PIL import Image
from crm.models import Person,Branch,Source,StaffProfile
from admissions.models import Document,DocumentVersion
from .models import PrivateLink,StudentRequest,StudentMessage


class StudentLinkTests(TestCase):
    def setUp(self):
        cache.clear();branch=Branch.objects.create(name='Link office',code='LINK');source=Source.objects.create(name='Link source')
        self.owner=User.objects.create_user(username='link-owner');StaffProfile.objects.create(user=self.owner,role='COUNSELOR',branch=branch)
        self.person=Person.objects.create(ref='BE-LINK-1',full_name='Link Student',source=source,branch=branch,owner=self.owner)
        self.other=Person.objects.create(ref='BE-LINK-2',full_name='Other Student',source=source,branch=branch,owner=self.owner)
        self.staff=APIClient();self.staff.force_authenticate(self.owner);self.client=APIClient();self.path=f'/api/v1/student-experience/staff/{self.person.pk}/'
        self.token='a'*43;self.link=PrivateLink.objects.create(person=self.person,token_hash=hashlib.sha256(self.token.encode()).hexdigest(),expires_at=timezone.now()+timezone.timedelta(days=1),created_by=self.owner)
        self.doc=Document.objects.create(person=self.person,title='Passport',type='PASSPORT',student_visible=True)
    def open(self):
        r=self.client.post('/api/v1/student-experience/entry/',{'token':self.token},format='json');self.assertEqual(r.status_code,200,r.data)
    def test_issuance_has_no_student_user_and_hash_only(self):
        before=User.objects.count();r=self.staff.post(self.path,{'operation':'link','days':1,'reason':'Student requested access'},format='json');self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(User.objects.count(),before);token=r.data['url'].split('#access=')[1]
        self.assertTrue(PrivateLink.objects.filter(token_hash=hashlib.sha256(token.encode()).hexdigest()).exists())
        self.link.refresh_from_db();self.assertFalse(self.link.active)
    def test_no_link_denied_and_student_scope(self):
        self.assertEqual(self.client.get('/api/v1/student-experience/dashboard/').status_code,403)
        self.open();otherdoc=Document.objects.create(person=self.other,title='Other private document',type='OTHER',student_visible=True)
        self.assertEqual(self.client.get(f'/api/v1/student-experience/documents/{otherdoc.pk}/').status_code,404)
        self.assertEqual(self.client.get('/api/v1/people/').status_code,403)
        self.assertEqual(self.client.get('/api/v1/student-experience/dashboard/')['Cache-Control'],'no-store')
        self.assertNotIn('_auth_user_id',self.client.session)
    def test_revocation_and_expiry_rechecked(self):
        self.open();self.staff.post(self.path,{'operation':'revoke','reason':'Access ended'},format='json')
        self.assertEqual(self.client.get('/api/v1/student-experience/dashboard/').status_code,403)
        self.link.active=True;self.link.expires_at=timezone.now()-timezone.timedelta(seconds=1);self.link.save()
        self.assertEqual(self.client.post('/api/v1/student-experience/entry/',{'token':self.token},format='json').status_code,400)
    def test_documents_private_by_default(self):
        self.doc.student_visible=False;self.doc.save();self.open()
        self.assertEqual(self.client.get('/api/v1/student-experience/dashboard/').data['documents'],[])
        self.assertEqual(self.client.get(f'/api/v1/student-experience/documents/{self.doc.pk}/').status_code,404)
    def test_upload_origin_no_user_and_review_required(self):
        self.open();p=f'/api/v1/student-experience/documents/{self.doc.pk}/'
        self.assertEqual(self.client.post(p,{'file':SimpleUploadedFile('bad.html',b'<script>bad</script>')},format='multipart').status_code,400)
        image=io.BytesIO();Image.new('RGB',(20,20),'white').save(image,format='PNG')
        r=self.client.post(p,{'file':SimpleUploadedFile('passport.png',image.getvalue())},format='multipart');self.assertEqual(r.status_code,200,r.data)
        self.doc.refresh_from_db();self.assertEqual(self.doc.status,'UPLOADED');self.assertIsNone(DocumentVersion.objects.get().uploaded_by)
        self.assertEqual(self.client.get(p).content,image.getvalue());self.assertEqual(self.client.patch(p,{'status':'VERIFIED'},format='json').status_code,405)
        with self.assertRaises(DatabaseError),transaction.atomic():DocumentVersion.objects.all().update(filename='Changed')
    def test_requests_are_reviewed_by_staff(self):
        action=StudentRequest.objects.create(person=self.person,title='Confirm details',instructions='Reply with a confirmation',created_by=self.owner);self.open()
        r=self.client.post('/api/v1/student-experience/messages/',{'body':'Confirmed','request_id':str(action.pk)},format='json');self.assertEqual(r.status_code,201,r.data)
        action.refresh_from_db();self.assertEqual(action.status,'SUBMITTED')
        r=self.staff.post(self.path,{'operation':'review','request_id':str(action.pk),'status':'COMPLETED','reason':'Reviewed confirmation'},format='json');self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(StudentMessage.objects.count(),2)
        self.assertEqual(self.staff.post(self.path,{'operation':'review','request_id':str(action.pk),'status':'NEEDS_CHANGES','reason':'Reopen'},format='json').status_code,400)
    def test_wrong_person_request_and_message_history_guard(self):
        action=StudentRequest.objects.create(person=self.other,title='Private request',instructions='Private',created_by=self.owner);self.open()
        self.assertEqual(self.client.post('/api/v1/student-experience/messages/',{'body':'Wrong','request_id':str(action.pk)},format='json').status_code,404)
        msg=StudentMessage.objects.create(person=self.person,direction='STUDENT',body='Original',access=self.link)
        with self.assertRaises(DatabaseError),transaction.atomic():StudentMessage.objects.filter(pk=msg.pk).update(body='Changed')
    def test_csrf_and_brute_force(self):
        c=APIClient(enforce_csrf_checks=True);p='/api/v1/student-experience/entry/'
        self.assertEqual(c.post(p,{'token':self.token},format='json').status_code,403)
        csrf=c.get(p).data['csrf_token'];cache.clear()
        for _ in range(10):c.post(p,{'token':'b'*43},format='json',HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(c.post(p,{'token':'b'*43},format='json',HTTP_X_CSRFTOKEN=csrf).status_code,429)
    def test_archived_or_merged_links_are_denied(self):
        self.open();self.person.archived_at=timezone.now();self.person.save();self.assertEqual(self.client.get('/api/v1/student-experience/dashboard/').status_code,403)
        self.person.archived_at=None;self.person.merged_into=self.other;self.person.save();self.assertEqual(self.client.get('/api/v1/student-experience/dashboard/').status_code,403)

    def test_merge_revokes_links_and_preserves_request_and_conversation(self):
        self.owner.staff.role='ADMIN';self.owner.staff.save()
        action=StudentRequest.objects.create(person=self.person,title='Pending request',instructions='Preserve this request',created_by=self.owner)
        StudentMessage.objects.create(person=self.person,direction='STUDENT',body='Historical student response',access=self.link)
        survivor_link=PrivateLink.objects.create(person=self.other,token_hash=hashlib.sha256(b'survivor').hexdigest(),expires_at=timezone.now()+timezone.timedelta(days=1),created_by=self.owner)
        response=self.staff.post('/api/v1/merges/',{'survivor_id':str(self.other.pk),'merged_id':str(self.person.pk),'reason':'Reviewed the same student identity'},format='json',HTTP_IDEMPOTENCY_KEY='student-link-merge-validation')
        self.assertEqual(response.status_code,200,response.data)
        self.link.refresh_from_db();survivor_link.refresh_from_db();action.refresh_from_db()
        self.assertFalse(self.link.active);self.assertFalse(survivor_link.active);self.assertEqual(action.person,self.other)
        data=self.staff.get(f'/api/v1/student-experience/staff/{self.other.pk}/').data
        self.assertEqual(data['messages'][0]['body'],'Historical student response')
