from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from .models import Branch, StaffProfile, Person, Source, FollowUp, Task, CounselingRecord
from admissions.models import Document

class FrontdeskWorkflowTests(TestCase):
    def setUp(self):
        self.branch=Branch.objects.create(name='Kathmandu',code='KTM')
        self.other_branch=Branch.objects.create(name='Pokhara',code='PKR')
        self.source=Source.objects.create(name='Walk-in')
        self.counselor=self.staff('counselor','COUNSELOR',self.branch)
        self.uk=self.staff('uk','COUNSELOR',self.branch)
        self.desk=self.staff('desk','FRONTDESK',self.branch)
        self.outside=self.staff('outside','FRONTDESK',self.other_branch)
        self.person=Person.objects.create(ref='BE-TEST-01',full_name='Visitor',branch=self.branch,owner=self.counselor,source=self.source,preferred_country='United Kingdom')
        self.client=APIClient();self.client.force_authenticate(self.counselor)
        self.path=f'/api/v1/people/{self.person.pk}/counseling/'
    def staff(self,name,role,branch):
        user=User.objects.create_user(name,password='test-password-123')
        StaffProfile.objects.create(user=user,role=role,branch=branch)
        return user
    def payload(self,**extra):
        return dict(summary='UK study options discussed',decisions='Prepare for next intake',documents_checklist=['Passport'],pending_documents='Academic transcripts',next_step='Confirm transcripts and intake',service_taken='',remarks='',**extra)
    def test_counseling_optional_documents_and_followup_handoff(self):
        due=(timezone.now()+timedelta(days=1)).isoformat()
        r=self.client.post(self.path,self.payload(followup_due_at=due,followup_owner_id=self.desk.pk,followup_notes='Ask when transcripts will be available.',scanning_owner_id=self.desk.pk,scanning_due_at=due),format='json',HTTP_IDEMPOTENCY_KEY='handoff-test')
        self.assertEqual(r.status_code,200,r.data)
        self.assertFalse(Document.objects.exists())
        followup=FollowUp.objects.get();self.assertEqual(followup.owner,self.desk);self.assertIn('transcripts',followup.notes)
        self.assertEqual(Task.objects.get().owner,self.desk)
        self.assertEqual(self.client.post(self.path,self.payload(followup_due_at=due,followup_owner_id=self.desk.pk,followup_notes='Ask when transcripts will be available.',scanning_owner_id=self.desk.pk,scanning_due_at=due),format='json',HTTP_IDEMPOTENCY_KEY='handoff-test').status_code,200)
        self.assertEqual(FollowUp.objects.count(),1)
        self.client.force_authenticate(self.desk)
        self.assertEqual(self.client.post(f'/api/v1/people/{self.person.pk}/reassign/',{'owner_id':self.uk.pk,'reason':'UK counselor handoff'},format='json').status_code,200)
        followup.refresh_from_db();self.assertEqual(followup.owner,self.desk)
        self.client.force_authenticate(self.desk)
        queue=self.client.get('/api/v1/followups/');self.assertEqual(queue.status_code,200);self.assertEqual(queue.data[0]['notes'],followup.notes)
        self.assertEqual(self.client.post(f'/api/v1/followups/{followup.pk}/complete/',{'outcome':'Student will bring transcripts'},format='json').status_code,200)
    def test_frontdesk_can_assign_counselor_but_cannot_change_counseling(self):
        self.client.force_authenticate(self.desk)
        r=self.client.post(f'/api/v1/people/{self.person.pk}/reassign/',{'owner_id':self.uk.pk,'reason':'Visitor is interested in the UK'},format='json')
        self.assertEqual(r.status_code,200,r.data);self.person.refresh_from_db();self.assertEqual(self.person.owner,self.uk)
        self.assertEqual(self.client.get(self.path).status_code,200)
        self.assertEqual(self.client.post(self.path,self.payload(),format='json').status_code,403)
        self.assertEqual(self.client.post(f'/api/v1/people/{self.person.pk}/convert/',{},format='json').status_code,403)
        self.assertEqual(self.client.get('/api/v1/settings/').status_code,403)
    def test_frontdesk_can_upload_optional_profile_document(self):
        self.client.force_authenticate(self.desk)
        r=self.client.post('/api/v1/admissions/documents/',{'person_id':str(self.person.pk),'type':'Passport','title':'Scanned passport','required':False},format='json')
        self.assertEqual(r.status_code,201,r.data);doc=Document.objects.get();self.assertFalse(doc.required)
        import io
        from PIL import Image
        buffer=io.BytesIO();Image.new('RGB',(10,10),'white').save(buffer,format='PNG')
        r=self.client.post(f'/api/v1/admissions/documents/{doc.pk}/',{'file':SimpleUploadedFile('passport.png',buffer.getvalue(),content_type='image/png')},format='multipart')
        self.assertEqual(r.status_code,200,r.data);doc.refresh_from_db();self.assertEqual(doc.version,1)
        self.assertEqual(self.client.patch(f'/api/v1/admissions/documents/{doc.pk}/',{'status':'UNDER_REVIEW'},format='json').status_code,403)
        self.assertEqual(self.client.get('/api/v1/admissions/documents/',{'person_id':str(self.person.pk)}).data['results'][0]['id'],str(doc.pk))
    def test_cross_branch_handoff_rolls_back(self):
        r=self.client.post(self.path,self.payload(followup_due_at=(timezone.now()+timedelta(days=1)).isoformat(),followup_owner_id=self.outside.pk),format='json')
        self.assertEqual(r.status_code,404);self.assertFalse(CounselingRecord.objects.exists());self.assertFalse(FollowUp.objects.exists())
        self.client.force_authenticate(self.outside)
        self.assertEqual(self.client.get(self.path).status_code,200)
        self.assertEqual(self.client.get('/api/v1/admissions/documents/',{'person_id':str(self.person.pk)}).status_code,200)
    def test_another_counselor_cannot_edit_record(self):
        self.client.force_authenticate(self.uk)
        self.assertEqual(self.client.post(self.path,self.payload(),format='json').status_code,404)
    def test_scanning_requires_due_date(self):
        r=self.client.post(self.path,self.payload(scanning_owner_id=self.desk.pk),format='json')
        self.assertEqual(r.status_code,400);self.assertFalse(CounselingRecord.objects.exists())

    def registration_code(self):
        from .models import Campaign
        from qr.models import QRCode
        campaign=Campaign.objects.create(name='Visitor registration',source=self.source)
        return QRCode.objects.create(label='Visitor form',branch=self.branch,campaign=campaign,created_by=self.counselor)

    def test_all_staff_can_view_shared_qr_and_only_their_staff_whatsapp(self):
        from qr.models import QRCode
        from .permissions import MATRIX
        qr=self.registration_code()
        wifi=QRCode.objects.create(label='Wi-Fi',branch=self.branch,campaign=qr.campaign,created_by=self.counselor,content_type='WIFI',content={'ssid':'Test office','password':'not-a-real-password'})
        own=QRCode.objects.create(label='Desk WhatsApp',branch=self.branch,campaign=qr.campaign,created_by=self.counselor,content_type='WHATSAPP',content={'phone':'+9779801234567','staff_user_id':self.desk.pk})
        foreign=QRCode.objects.create(label='Counselor WhatsApp',branch=self.branch,campaign=qr.campaign,created_by=self.counselor,content_type='WHATSAPP',content={'phone':'+9779801234568','staff_user_id':self.uk.pk})
        for role in MATRIX:
            user=self.desk if role=='FRONTDESK' else self.staff('qr-'+role,role,self.branch)
            self.client.force_authenticate(user)
            r=self.client.get('/api/v1/qr/library/');self.assertEqual(r.status_code,200,r.data)
            ids={q['id'] for q in r.data['results']};self.assertIn(str(qr.pk),ids);self.assertIn(str(wifi.pk),ids)
            self.assertNotIn('password',next(q['content'] for q in r.data['results'] if q['id']==str(wifi.pk)))
            if role=='FRONTDESK':self.assertIn(str(own.pk),ids);self.assertNotIn(str(foreign.pk),ids)
        self.client.force_authenticate(self.desk)
        self.assertEqual(self.client.get(f'/api/v1/qr/{foreign.pk}/download/').status_code,404)
        self.assertEqual(self.client.patch(f'/api/v1/qr/{qr.pk}/',{'label':'Changed'},format='json').status_code,403)
        self.client.force_authenticate(self.outside)
        self.assertEqual(self.client.get('/api/v1/qr/library/').data['results'],[])

    def test_paper_intake_requires_otp_before_manual_counselor_assignment(self):
        from unittest.mock import patch
        from intake.models import IntakeSubmission
        class Provider:
            name='test';code=''
            def send(self,phone,code,delivery):self.code=code;return 'test-message'
        provider=Provider();qr=self.registration_code();self.client.force_authenticate(self.desk)
        payload={'code':qr.code,'full_name':'Paper visitor','phone':'+9779801234599','channel':'WHATSAPP','consent':True,'consent_channels':['WHATSAPP','CALL'],'preferred_country':'United Kingdom','preferred_university':'Test university','alternate_phone':'+9779801234598','heard_about_us':'Referral','best_contact_method':'WHATSAPP','education':[{'level':'Bachelor','institute':'Test institute'}]}
        with patch('intake.services.get_provider',return_value=provider):
            r=self.client.post('/api/v1/intake/paper/',payload,format='json',HTTP_IDEMPOTENCY_KEY='paper-test')
            self.assertEqual(r.status_code,201,r.data)
            repeat=self.client.post('/api/v1/intake/paper/',payload,format='json',HTTP_IDEMPOTENCY_KEY='paper-test');self.assertEqual(repeat.status_code,200)
        self.assertEqual(Person.objects.count(),1);self.assertEqual(IntakeSubmission.objects.count(),1)
        credentials=r.data;verify=f'/api/v1/intake/paper/{credentials["id"]}/verify/'
        wrong=self.client.post(verify,{'code':'wrong'},format='json',HTTP_X_RESUME_TOKEN=credentials['resume_token']);self.assertEqual(wrong.status_code,400)
        verified=self.client.post(verify,{'code':provider.code},format='json',HTTP_X_RESUME_TOKEN=credentials['resume_token']);self.assertEqual(verified.status_code,200,verified.data)
        retry=self.client.post(verify,{'code':provider.code},format='json',HTTP_X_RESUME_TOKEN=credentials['resume_token']);self.assertEqual(retry.data['person_id'],verified.data['person_id'])
        person=Person.objects.get(pk=verified.data['person_id']);self.assertIsNone(person.owner);self.assertEqual(person.preferred_university,'Test university');self.assertEqual(person.education.count(),1)
        self.assertFalse(person.followups.exists());self.assertIsNone(person.next_action_due_at)
        self.assertEqual(IntakeSubmission.objects.get().verified_by_staff,self.desk)
        self.assertEqual(self.client.post(f'/api/v1/people/{person.pk}/reassign/',{'owner_id':self.uk.pk,'reason':'UK counselor'},format='json').status_code,200)
        person.refresh_from_db();self.assertEqual(person.owner,self.uk)
        self.assertEqual(self.client.post('/api/v1/intake/assisted/',payload,format='json').status_code,403)

    def test_each_completed_followup_keeps_day_time_outcome_and_reschedule_history(self):
        from unittest.mock import patch
        from .models import AuditEvent, Activity
        first_time=timezone.now()-timedelta(days=2)
        second_time=first_time+timedelta(days=1)
        ids=[]
        for index,when in enumerate([first_time,second_time]):
            r=self.client.post(f'/api/v1/people/{self.person.pk}/followups/',{'subject':f'Visitor call {index+1}','method':'CALL','due_at':when.isoformat(),'notes':'Discuss pending documents'},format='json')
            self.assertEqual(r.status_code,201,r.data);ids.append(r.data['id'])
            with patch('crm.api.timezone.now',return_value=when):
                r=self.client.post(f'/api/v1/followups/{ids[-1]}/complete/',{'outcome':f'Day {index+1} discussion recorded'},format='json')
            self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(self.client.get('/api/v1/followups/').data,[])
        history=self.client.get('/api/v1/followups/?include_completed=1').data
        self.assertEqual(len(history),2);self.assertEqual([x['id'] for x in history],list(reversed(ids)))
        self.assertEqual(FollowUp.objects.get(pk=ids[0]).completed_at,first_time)
        self.assertEqual(FollowUp.objects.get(pk=ids[1]).completed_at,second_time)
        r=self.client.post(f'/api/v1/followups/{ids[0]}/update/',{'status':'OPEN','notes':'Overwrite'},format='json')
        self.assertEqual(r.status_code,400)
        self.assertEqual(FollowUp.objects.get(pk=ids[0]).outcome,'Day 1 discussion recorded')
        third=FollowUp.objects.create(person=self.person,owner=self.counselor,subject='Next call',due_at=second_time+timedelta(days=2),notes='Original instructions')
        old_due=third.due_at.isoformat()
        r=self.client.post(f'/api/v1/followups/{third.pk}/update/',{'due_at':(third.due_at+timedelta(days=1)).isoformat(),'notes':'New instructions'},format='json');self.assertEqual(r.status_code,200,r.data)
        audit=AuditEvent.objects.get(action='FOLLOWUP_CHANGED');self.assertEqual(audit.old['due_at'],old_due);self.assertEqual(audit.old['notes'],'Original instructions')
        self.assertIn('Original instructions',Activity.objects.get(subject='Follow-up updated: Next call').notes)

    def test_initial_assignment_needs_no_reason_but_reassignment_does(self):
        self.client.force_authenticate(self.desk)
        self.person.owner=None;self.person.save(update_fields=['owner'])
        path=f'/api/v1/people/{self.person.pk}/reassign/'
        result=self.client.post(path,{'owner_id':self.uk.pk},format='json')
        self.assertEqual(result.status_code,200,result.data)
        self.person.refresh_from_db();self.assertEqual(self.person.owner,self.uk)
        self.assertEqual(self.client.post(path,{'owner_id':self.counselor.pk},format='json').status_code,400)
        self.assertEqual(self.client.post(path,{'owner_id':self.uk.pk},format='json').status_code,200)

    def test_counselor_can_undo_own_conversion_without_reassign_permission(self):
        self.person.stage='STUDENT';self.person.lead_status='CONVERTED';self.person.student_state='ACTIVE';self.person.converted_at=timezone.now();self.person.save()
        self.client.force_authenticate(self.counselor)
        path=f'/api/v1/people/{self.person.pk}/lifecycle/'
        self.assertEqual(self.client.post(path,{'revert_to_lead':True},format='json').status_code,400)
        result=self.client.post(path,{'revert_to_lead':True,'reason':'Converted by mistake'},format='json')
        self.assertEqual(result.status_code,200,result.data)
        self.person.refresh_from_db();self.assertEqual(self.person.stage,'LEAD');self.assertEqual(self.person.lead_status,'CONTACTED')
        self.client.force_authenticate(self.desk)
        self.assertEqual(self.client.post(path,{'revert_to_lead':True,'reason':'Mistake'},format='json').status_code,403)

    def test_rollback_restores_status_from_latest_conversion(self):
        from .models import AuditEvent
        self.client.force_authenticate(self.counselor)
        for status in ['NEW','COUNSELING','INTERESTED','DOCUMENT_COLLECTION','APPLICATION_READY']:
            self.person.stage='LEAD';self.person.lead_status=status;self.person.save()
            converted=self.client.post(f'/api/v1/people/{self.person.pk}/convert/')
            self.assertEqual(converted.status_code,200,converted.data)
            reverted=self.client.post(f'/api/v1/people/{self.person.pk}/lifecycle/',{'revert_to_lead':True,'reason':'Converted by mistake'},format='json')
            self.assertEqual(reverted.status_code,200,reverted.data)
            self.person.refresh_from_db();self.assertEqual(self.person.stage,'LEAD');self.assertEqual(self.person.lead_status,status)
            self.assertIsNone(self.person.converted_at)
            event=AuditEvent.objects.filter(action='LIFECYCLE_CHANGED').latest('pk');self.assertEqual(event.new['status'],status)

    def test_all_staff_can_view_other_students_without_grants(self):
        other=Person.objects.create(ref='BE-OTHER',full_name='Other branch visitor',branch=self.other_branch,owner=None,source=self.source)
        for role in StaffProfile.Role.values:
            user=self.staff('shared-'+role,role,self.branch);self.client.force_authenticate(user)
            rows=self.client.get('/api/v1/people/');self.assertEqual(rows.status_code,200,rows.data);self.assertEqual(rows.data['count'],2)
            profile=self.client.get(f'/api/v1/people/{other.pk}/');self.assertEqual(profile.status_code,200,profile.data);self.assertFalse(profile.data.get('masked',False))
        self.client.force_authenticate(self.counselor)
        self.assertEqual(self.client.patch(f'/api/v1/people/{other.pk}/profile/',{'full_name':'Unauthorized'},format='json').status_code,404)

    def test_study_suggestions_preserve_multiple_recommendations_and_permissions(self):
        from .models import CounselingSuggestion
        self.client.force_authenticate(self.counselor)
        url=f'/api/v1/people/{self.person.pk}/counseling/suggestions/'
        first={'country':'United Kingdom','study_level':'Master','university':'University A','course':'Computing','intake':'September 2027','notes':'Discussed eligibility'}
        self.assertEqual(self.client.post(url,first,format='json',HTTP_IDEMPOTENCY_KEY='suggestion-one').status_code,201)
        self.assertEqual(self.client.post(url,first,format='json',HTTP_IDEMPOTENCY_KEY='suggestion-one').status_code,201)
        second={**first,'university':'University B','course':'Data Science','intake':'January 2028'}
        self.assertEqual(self.client.post(url,second,format='json',HTTP_IDEMPOTENCY_KEY='suggestion-two').status_code,201)
        self.assertEqual(CounselingSuggestion.objects.count(),2)
        rows=self.client.get(url).data['results'];self.assertEqual([r['university'] for r in rows],['University B','University A']);self.assertTrue(rows[0]['created_at']);self.assertEqual(rows[0]['suggested_by'],self.counselor.username)
        self.client.force_authenticate(self.desk)
        self.assertEqual(len(self.client.get(url).data['results']),2)
        self.assertEqual(self.client.post(url,second,format='json',HTTP_IDEMPOTENCY_KEY='suggestion-desk').status_code,403)
        self.client.force_authenticate(self.uk)
        self.assertEqual(self.client.post(url,second,format='json',HTTP_IDEMPOTENCY_KEY='suggestion-other').status_code,404)
        self.assertEqual(CounselingSuggestion.objects.count(),2)

    def test_multiple_study_options_save_together_without_duplicate_retries(self):
        from .models import CounselingSuggestion
        self.client.force_authenticate(self.counselor)
        url=f'/api/v1/people/{self.person.pk}/counseling/suggestions/'
        values={'suggestions':[{'country':'UK','university':'A','course':'Computing','intake':'September 2027'},{'country':'UK','university':'B','course':'Business','intake':'January 2028'}]}
        for attempt in range(2):self.assertEqual(self.client.post(url,values,format='json',HTTP_IDEMPOTENCY_KEY='batch-suggestions').status_code,201)
        self.assertEqual(CounselingSuggestion.objects.count(),2)
        bad={'suggestions':[{'country':'Canada','university':'C'},{'country':''}]}
        self.assertEqual(self.client.post(url,bad,format='json',HTTP_IDEMPOTENCY_KEY='invalid-batch').status_code,400)
        self.assertEqual(CounselingSuggestion.objects.count(),2)

    def test_search_catalog_adds_shared_options_without_case_duplicates(self):
        from .models import CounselingOption
        url='/api/v1/counseling/options/'
        self.client.force_authenticate(self.counselor)
        for kind,name in [('COUNTRY','Nepal'),('UNIVERSITY','University of East London'),('COURSE','MSc Computing'),('INTAKE','September 2027')]:
            result=self.client.post(url,{'kind':kind,'name':name},format='json');self.assertEqual(result.status_code,201,result.data)
            repeat=self.client.post(url,{'kind':kind,'name':name.lower()},format='json');self.assertEqual(repeat.status_code,200);self.assertEqual(repeat.data['id'],result.data['id'])
            self.client.force_authenticate(self.uk);self.assertEqual(self.client.get(url,{'kind':kind}).data['results'][0]['name'],name)
        self.assertEqual(CounselingOption.objects.count(),4)
        self.client.force_authenticate(self.desk);self.assertEqual(self.client.get(url,{'kind':'UNIVERSITY'}).status_code,200)
        self.assertEqual(self.client.post(url,{'kind':'UNIVERSITY','name':'Other'},format='json').status_code,403)

    def test_multiple_uploads_then_edit_each_document_name_and_type(self):
        import io
        from PIL import Image
        self.client.force_authenticate(self.desk)
        buffer=io.BytesIO();Image.new('RGB',(10,10),'white').save(buffer,format='PNG')
        def upload(name):return SimpleUploadedFile(name,buffer.getvalue(),content_type='image/png')
        r=self.client.post('/api/v1/admissions/documents/upload/',{'person_id':str(self.person.pk),'files':[upload('passport.png'),upload('class10.png'),upload('class12.png'),upload('degree.png'),upload('marks.png')]},format='multipart',HTTP_IDEMPOTENCY_KEY='batch-documents')
        self.assertEqual(r.status_code,201,r.data);self.assertEqual(len(r.data['results']),5)
        self.assertEqual(Document.objects.count(),5)
        r=self.client.post('/api/v1/admissions/documents/upload/',{'person_id':str(self.person.pk),'files':[upload('passport.png'),upload('class10.png'),upload('class12.png'),upload('degree.png'),upload('marks.png')]},format='multipart',HTTP_IDEMPOTENCY_KEY='batch-documents')
        self.assertEqual(r.status_code,201,r.data);self.assertEqual(Document.objects.count(),5)
        doc=Document.objects.first()
        self.assertEqual(doc.status,'UPLOADED');self.assertEqual(doc.type,'Unclassified');self.assertFalse(doc.required)
        r=self.client.patch(f'/api/v1/admissions/documents/{doc.pk}/',{'type':'Class 10th','title':'Class 10 transcript'},format='json')
        self.assertEqual(r.status_code,200,r.data);doc.refresh_from_db();self.assertEqual(doc.type,'Class 10th');self.assertEqual(doc.title,'Class 10 transcript')
        self.client.force_authenticate(self.counselor)
        rows=self.client.get('/api/v1/admissions/documents/',{'person_id':str(self.person.pk)}).data['results']
        self.assertEqual(len(rows),5);self.assertTrue(all(row['version']==1 and row['status']=='UPLOADED' for row in rows))

    def test_invalid_batch_rolls_back_all_files_and_large_file_has_no_app_limit(self):
        import io
        from PIL import Image
        from admissions.api import validate_upload
        self.client.force_authenticate(self.desk)
        buffer=io.BytesIO();Image.new('RGB',(10,10),'white').save(buffer,format='PNG')
        valid=SimpleUploadedFile('valid.png',buffer.getvalue(),content_type='image/png')
        invalid=SimpleUploadedFile('bad.png',b'not an image',content_type='image/png')
        r=self.client.post('/api/v1/admissions/documents/upload/',{'person_id':str(self.person.pk),'files':[valid,invalid]},format='multipart')
        self.assertEqual(r.status_code,400);self.assertFalse(Document.objects.exists())
        large=SimpleUploadedFile('large.png',buffer.getvalue()+b'\0'*(6*1024*1024),content_type='image/png')
        self.assertGreater(large.size,5*1024*1024)
        raw,mime,_=validate_upload(large);self.assertEqual(mime,'image/png');self.assertGreater(len(raw),5*1024*1024)
