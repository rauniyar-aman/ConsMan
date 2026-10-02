import io
from datetime import timedelta
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError,transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from PIL import Image
from crm.models import Branch,Person,Source,StaffProfile,TeamMember
from .models import *
from .services import PHASE_TWO_STATES


class AdmissionTests(TestCase):
    def setUp(self):
        self.branch=Branch.objects.create(name='Test branch',code='ADM')
        self.admin=self.user('admin','ADMIN');self.owner=self.user('counselor','COUNSELOR');self.other=self.user('other','COUNSELOR');self.docs=self.user('docs','DOCS')
        self.person=Person.objects.create(ref='BE-TEST-1',full_name='Student One',branch=self.branch,owner=self.owner,source=Source.objects.create(name='Admissions test'))
        self.uni=University.objects.create(name='Test University',country='Testland')
        self.campus=Campus.objects.create(university=self.uni,name='Main campus')
        self.course=Course.objects.create(university=self.uni,name='Computing',level='Bachelor')
        self.intake=Intake.objects.create(name='Next intake',start_date=timezone.localdate()+timedelta(days=100),application_deadline=timezone.now()+timedelta(days=50))
        self.offering=CourseOffering.objects.create(course=self.course,campus=self.campus,intake=self.intake,fee=10000)
        self.workflow=WorkflowTemplate.objects.create(destination='Testland',version=1,milestones=PHASE_TWO_STATES,required_documents=[{'type':'PASSPORT','title':'Passport','required':True}],task_templates={'DRAFT':['Confirm student details'],'SUBMITTED':['Follow up with institution']},created_by=self.admin)
        self.client=APIClient();self.client.force_authenticate(self.owner)
    def user(self,name,role):
        u=User.objects.create_user(username=name,password='test-only-password');StaffProfile.objects.create(user=u,role=role,branch=self.branch);return u
    def create(self):
        r=self.client.post('/api/v1/admissions/applications/',{'person_id':str(self.person.pk),'offering_id':self.offering.pk,'workflow_id':self.workflow.pk},format='json');self.assertEqual(r.status_code,201,r.data);return Application.objects.get(pk=r.data['id'])
    def patch(self,app,**data):return self.client.patch(f'/api/v1/admissions/applications/{app.pk}/',data,format='json')
    def waive(self,app):
        d=app.documents.get();r=self.client.patch(f'/api/v1/admissions/documents/{d.pk}/',{'status':'WAIVED','reason':'Verified through institution'},format='json');self.assertEqual(r.status_code,200,r.data)
    def test_multiple_apps_pin_workflow_and_generate_tasks(self):
        a=self.create();b=self.create();self.assertNotEqual(a.ref,b.ref);self.assertEqual(a.documents.count(),1);self.assertEqual(a.generated_tasks.count(),1);self.assertEqual(a.deadlines.count(),2)
        self.assertEqual(self.client.get(f'/api/v1/admissions/applications/{a.pk}/').data['workflow_version'],1)
        self.assertEqual(self.person.applications.count(),2)
    def test_ready_requires_documents_and_global_blockers(self):
        a=self.create();self.assertEqual(self.patch(a,state='READY',reason='Review ready').status_code,400)
        self.waive(a);blocker=Blocker.objects.create(person=self.person,type='FINANCIAL',description='Missing evidence',assigned_to=self.owner,created_by=self.owner)
        self.assertEqual(self.patch(a,state='READY',reason='Review ready').status_code,400)
        self.client.patch(f'/api/v1/admissions/blockers/{blocker.pk}/',{'reason':'Evidence received'},format='json')
        self.assertEqual(self.patch(a,state='READY',reason='Documents reviewed').status_code,200)
        self.assertEqual(self.patch(a,state='SUBMITTED').status_code,200);self.assertEqual(a.generated_tasks.count(),2)
    def test_scoped_applications_and_document_staff(self):
        a=self.create();self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/v1/admissions/applications/').data['count'],1)
        self.assertEqual(self.patch(a,state='WITHDRAWN',reason='Test').status_code,404)
        TeamMember.objects.create(person=self.person,user=self.docs)
        self.client.force_authenticate(self.docs)
        self.assertEqual(self.client.get(f'/api/v1/admissions/applications/{a.pk}/').status_code,200)
        self.assertEqual(self.patch(a,notes='Unauthorized').status_code,403)
        self.waive(a)
    def test_document_upload_review_download_versions(self):
        a=self.create();d=a.documents.get();image=io.BytesIO();Image.new('RGB',(20,20),'white').save(image,format='PNG');raw=image.getvalue()
        path=f'/api/v1/admissions/documents/{d.pk}/'
        for version in [1,2]:
            r=self.client.post(path,{'file':SimpleUploadedFile('passport.png',raw)},format='multipart');self.assertEqual(r.status_code,200,r.data);self.assertEqual(r.data['version'],version)
        for status in ['UNDER_REVIEW','VERIFIED']:
            r=self.client.patch(path,{'status':status},format='json');self.assertEqual(r.status_code,200,r.data)
        r=self.client.get(path+'versions/1/');self.assertEqual(r.status_code,200);self.assertEqual(r.content,raw);self.assertIn('no-store',r['Cache-Control'])
        self.client.force_authenticate(self.other);self.assertEqual(self.client.get(path+'versions/1/').status_code,200)
        self.assertEqual(self.client.post(path,{'file':SimpleUploadedFile('passport.png',raw)},format='multipart').status_code,404)
        self.client.force_authenticate(self.owner);self.assertEqual(self.client.post(path,{'file':SimpleUploadedFile('bad.html',b'<script/>')},format='multipart').status_code,400)
        self.assertEqual(d.versions.count(),2)
    def test_expiry_blocks_ready_and_invalid_review_rejected(self):
        a=self.create();d=a.documents.get();d.status='VERIFIED';d.version=1;d.expires_at=timezone.now()-timedelta(days=1);d.save()
        self.assertEqual(self.patch(a,state='READY',reason='Review').status_code,400)
        self.assertEqual(self.client.get(f'/api/v1/admissions/documents/{d.pk}/').data['status'],'EXPIRED')
        self.assertEqual(self.client.patch(f'/api/v1/admissions/documents/{d.pk}/',{'status':'VERIFIED'},format='json').status_code,400)
    def test_offer_and_terminal_rules(self):
        a=self.create();self.assertEqual(self.patch(a,state='OFFER_RECEIVED',reason='Skip').status_code,400)
        r=self.client.post(f'/api/v1/admissions/applications/{a.pk}/offers/',{'type':'CONDITIONAL','conditions':['Final transcript'],'expires_at':(timezone.now()+timedelta(days=10)).isoformat()},format='json');self.assertEqual(r.status_code,201,r.data)
        a.refresh_from_db();self.assertEqual(a.state,'OFFER_RECEIVED');self.assertEqual(a.offers.get().conditions,['Final transcript'])
        self.assertEqual(self.patch(a,state='OFFER_ACCEPTED').status_code,400)
        self.assertEqual(self.patch(a,state='WITHDRAWN').status_code,400)
        self.assertEqual(self.patch(a,state='WITHDRAWN',reason='Student decided to withdraw').status_code,200)
        self.assertEqual(self.patch(a,state='DRAFT',reason='Retry').status_code,400)
    def test_admin_catalog_and_immutable_workflows(self):
        path='/api/v1/admissions/catalog/workflows/'
        self.assertEqual(self.client.post(path,{},format='json').status_code,403)
        self.client.force_authenticate(self.admin)
        data={'destination':'Testland','milestones':PHASE_TWO_STATES,'required_documents':[]}
        r=self.client.post(path,data,format='json');self.assertEqual(r.status_code,201,r.data);self.assertEqual(r.data['version'],2)
        self.assertEqual(self.client.patch(path+str(self.workflow.pk)+'/',{'milestones':['DRAFT','OFFER_RECEIVED']},format='json').status_code,400)
        self.assertEqual(self.client.post(path,{**data,'milestones':[{},'OFFER_RECEIVED']},format='json').status_code,400)
        with self.assertRaises(DatabaseError),transaction.atomic():WorkflowTemplate.objects.filter(pk=self.workflow.pk).update(milestones=[])
        with self.assertRaises(DatabaseError),transaction.atomic():self.workflow.delete()
    def test_events_and_versions_append_only(self):
        a=self.create()
        with self.assertRaises(DatabaseError),transaction.atomic():a.events.update(reason='Tampered')
        with self.assertRaises(DatabaseError),transaction.atomic():a.events.all().delete()
    def test_matching_and_preferences(self):
        self.offering.minimum_academic_percent=70;self.offering.save()
        path=f'/api/v1/admissions/preferences/{self.person.pk}/'
        self.assertEqual(self.client.get(path).data['matches'][0]['result'],'REVIEW_REQUIRED')
        r=self.client.patch(path,{'countries':['Testland'],'academic_percent':80,'maximum_fee':12000,'currency':'USD'},format='json');self.assertEqual(r.status_code,200,r.data);self.assertEqual(r.data['matches'][0]['result'],'MATCHED')
        r=self.client.patch(path,{'maximum_fee':5000},format='json');self.assertEqual(r.data['matches'][0]['result'],'NOT_MATCHED')
        self.assertEqual(self.client.patch(path,{'academic_percent':101},format='json').status_code,400)
    def test_offering_hierarchy_validation(self):
        self.client.force_authenticate(self.admin);other=University.objects.create(name='Other University',country='Testland');campus=Campus.objects.create(university=other,name='Other campus')
        r=self.client.post('/api/v1/admissions/catalog/offerings/',{'course':self.course.pk,'campus':campus.pk,'intake':self.intake.pk},format='json');self.assertEqual(r.status_code,400)
    def test_deferral_replaces_both_intake_deadlines(self):
        a=self.create();self.patch(a,state='DEFERRED',reason='Next intake requested')
        intake=Intake.objects.create(name='Later intake',start_date=self.intake.start_date+timedelta(days=180),application_deadline=timezone.now()+timedelta(days=180))
        offering=CourseOffering.objects.create(course=self.course,campus=self.campus,intake=intake)
        self.assertEqual(self.patch(a,offering_id=offering.pk,reason='Institution approved deferral').status_code,200)
        self.assertEqual(a.deadlines.filter(status='OPEN').count(),2);self.assertEqual(a.deadlines.filter(status='SUPERSEDED').count(),2)
    def test_application_creation_retries_do_not_duplicate(self):
        data={'person_id':str(self.person.pk),'offering_id':self.offering.pk,'workflow_id':self.workflow.pk}
        for attempt in range(2):
            r=self.client.post('/api/v1/admissions/applications/',data,format='json',HTTP_IDEMPOTENCY_KEY='admission-create-test');self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(Application.objects.count(),1)
    def test_merge_and_reversal_preserve_application_documents(self):
        a=self.create();survivor=Person.objects.create(ref='BE-TEST-2',full_name='Survivor',branch=self.branch,owner=self.owner,source=self.person.source)
        self.client.force_authenticate(self.admin)
        r=self.client.post('/api/v1/merges/',{'survivor_id':str(survivor.pk),'merged_id':str(self.person.pk),'reason':'Confirmed duplicate'},format='json',HTTP_IDEMPOTENCY_KEY='merge-admission-test');self.assertEqual(r.status_code,200,r.data)
        a.refresh_from_db();self.assertEqual(a.person_id,survivor.pk);self.assertEqual(a.documents.get().person_id,survivor.pk)
        r=self.client.post(f"/api/v1/merges/{r.data['merge_id']}/reverse/",{'reason':'Reviewed reversal'},format='json');self.assertEqual(r.status_code,200,r.data)
        a.refresh_from_db();self.assertEqual(a.person_id,self.person.pk);self.assertEqual(a.documents.get().person_id,self.person.pk)
