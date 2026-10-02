import io
import json
import uuid
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch
import pyotp
from django.contrib.auth.models import User
from django.db import transaction,DatabaseError
from django.test import TestCase,override_settings
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework.exceptions import PermissionDenied,ValidationError
from .tests import CrmTests
from .models import *
from .permissions import MATRIX,scope,scoped_people
from .services import duplicate_signals,automatic_owner
from .calendar import business_due
from .authentication import cipher
from qr.models import QRCode,QRAsset
from qr.api import create_asset
from qr.renderer import render,safe_design
from intake.models import IntakeSubmission,OtpChallenge,MessageDelivery
from data_import.models import ImportBatch

class MemoryProvider:
    name='test-only'
    def __init__(self):self.codes=[]
    def send(self,phone,code,delivery):
        self.codes.append(code)
        assert IntakeSubmission.objects.filter(pk=delivery.challenge.submission_id,status='UNVERIFIED').exists()
        return 'mock-message-id'

@override_settings(DEBUG=True,INTAKE_DEV_BYPASS=True)
class PhaseOneTests(TestCase):
    user=CrmTests.user
    payload=CrmTests.payload
    create=CrmTests.create
    def setUp(self):
        CrmTests.setUp(self)
        self.client.force_authenticate(self.manager)
        self.campaign=Campaign.objects.create(name='Walk-in KTM',source=self.source)
        self.calendar=BusinessCalendar.objects.create(branch=self.branch)
        for trigger,hours in [('FIRST_CONTACT',24),('HOT_CONTACT',4),('ASSIGNMENT',2),('ACCESS',8)]:SlaRule.objects.create(name=trigger,trigger=trigger,target_business_hours=hours)
        self.qr=QRCode.objects.create(label='Test desk',branch=self.branch,campaign=self.campaign,created_by=self.manager)
        self.provider=MemoryProvider()
        self.public=APIClient()
    def submit(self,**kwargs):
        values={'code':self.qr.code,'full_name':'Visitor Example','phone':'9801234599','consent':True,'turnstile_token':'development',**kwargs}
        with patch('intake.services.get_provider',return_value=self.provider):
            result=self.public.post('/api/public/intake/submissions/',values,format='json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(result.status_code,201,result.data)
        return result.data
    def test_business_calendar_weekend_holiday_24_hours(self):
        self.calendar.holidays=['2026-10-04'];self.calendar.save()
        start=datetime(2026,10,2,16,tzinfo=ZoneInfo('Asia/Kathmandu'))
        self.assertEqual(business_due(start,24,self.calendar),datetime(2026,10,8,12,tzinfo=ZoneInfo('Asia/Kathmandu')))
    def test_permission_matrix_all_actions(self):
        actions=set().union(*(set(v) for v in MATRIX.values()))
        for role in MATRIX:
            user=self.user('matrix_'+role,role,self.branch)
            for action in actions:
                with self.subTest(role=role,action=action):
                    if action in MATRIX[role]:self.assertEqual(scope(user,action),MATRIX[role][action])
                    else:
                        with self.assertRaises(PermissionDenied):scope(user,action)
    def test_documentation_assigned_scope(self):
        person=Person.objects.get(pk=self.create().data['id'])
        docs=self.user('docs','DOCS',self.branch)
        self.client.force_authenticate(docs)
        self.assertEqual(self.client.get('/api/v1/people/').data['count'],0)
        TeamMember.objects.create(person=person,user=docs)
        self.assertEqual(self.client.get('/api/v1/people/').data['count'],1)
        self.assertEqual(self.client.patch(f'/api/v1/people/{person.pk}/profile/',{'full_name':'Unauthorized'},format='json').status_code,400)
        self.assertEqual(self.client.post(f'/api/v1/people/{person.pk}/convert/').status_code,403)
    def test_lost_hold_backward_and_frozen_conversion(self):
        person=self.create().data['id'];url=f'/api/v1/people/{person}/lifecycle/'
        self.assertEqual(self.client.post(url,{'status':'LOST'},format='json').status_code,404)
        reason=LostReason.objects.create(name='Other',note_required=True)
        self.assertEqual(self.client.post(url,{'status':'LOST','lost_reason_id':reason.pk},format='json').status_code,400)
        self.assertEqual(self.client.post(url,{'status':'COUNSELING'},format='json').status_code,400)
        self.assertEqual(self.client.post(url,{'status':'CONTACTED'},format='json').status_code,200)
        self.assertEqual(self.client.post(url,{'status':'NEW'},format='json').status_code,400)
        self.assertEqual(self.client.post(url,{'status':'ON_HOLD','hold_until':(timezone.now()+timedelta(days=2)).isoformat()},format='json').status_code,200)
        self.assertEqual(FollowUp.objects.filter(person_id=person).count(),1)
    def test_task_next_action_and_completion(self):
        person=self.create().data['id'];due=timezone.now()+timedelta(hours=1)
        response=self.client.post('/api/v1/tasks/',{'person_id':person,'title':'Prepare list','due_at':due.isoformat()},format='json')
        self.assertEqual(response.status_code,201,response.data)
        p=Person.objects.get(pk=person);self.assertEqual(p.next_action_type,'TASK')
        response=self.client.post(f'/api/v1/tasks/{response.data["id"]}/update/',{'status':'COMPLETED'},format='json')
        self.assertEqual(response.status_code,200)
        p.refresh_from_db();self.assertIsNone(p.next_action_due_at)
    def test_access_grant_masking_approval_and_notification(self):
        person=self.create(owner_id=self.other.pk).data['id']
        self.client.force_authenticate(self.owner)
        results=self.client.get('/api/v1/discovery/?search=9801234567').data
        self.assertEqual(len(results),1);self.assertNotIn('full_name',results[0]);self.assertNotIn('phone',results[0])
        response=self.client.post('/api/v1/access-requests/',{'person_id':person,'reason':'Shared counseling case'},format='json')
        self.assertEqual(response.status_code,201,response.data)
        self.client.force_authenticate(self.manager)
        decision=self.client.post(f'/api/v1/access-requests/{response.data["id"]}/decide/',{'approved':True,'reason':'Manager reviewed'},format='json')
        self.assertEqual(decision.status_code,200,decision.data)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(f'/api/v1/people/{person}/').status_code,200)
        self.assertTrue(Notification.objects.filter(recipient=self.owner,type='ACCESS_DECISION').exists())
    def test_audit_database_rejects_update_and_delete(self):
        self.create();event=AuditEvent.objects.first()
        for action in ['update','delete']:
            with self.subTest(action=action):
                with self.assertRaises(DatabaseError),transaction.atomic():
                    query=AuditEvent.objects.filter(pk=event.pk)
                    query.update(action='tampered') if action=='update' else query.delete()
        self.assertEqual(AuditEvent.objects.get(pk=event.pk).action,'PERSON_CREATED')
    def test_mfa_enroll_and_login_challenge(self):
        admin=self.user('phase-admin','ADMIN',self.branch);client=APIClient(enforce_csrf_checks=True)
        token=client.get('/api/v1/auth/session/').data['csrf_token']
        response=client.post('/api/v1/auth/login/',{'username':admin.username,'password':'test-password-123'},format='json',HTTP_X_CSRFTOKEN=token)
        self.assertTrue(response.data['mfa_required'])
        setup=client.post('/api/v1/auth/mfa/setup/',{},format='json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(setup.status_code,200,setup.data)
        code=pyotp.TOTP(setup.data['secret']).now()
        response=client.post('/api/v1/auth/mfa/verify/',{'code':code},format='json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(client.get('/api/v1/people/').status_code,200)
        self.assertNotEqual(MfaDevice.objects.get(user=admin).encrypted_secret,setup.data['secret'])
    def test_duplicate_tiers_shared_and_exact(self):
        person=Person.objects.get(pk=self.create().data['id'])
        results=duplicate_signals('+9779801234567','suman@example.com')
        self.assertEqual(results[0]['confidence'],'EXACT')
        person.contacts.filter(type='PHONE').update(is_shared=True)
        results=duplicate_signals('+9779801234567')
        self.assertEqual(results[0]['confidence'],'MEDIUM')
    def test_merge_moves_history_and_archives(self):
        first=self.create().data['id'];second=self.create(full_name='Another Person',phone='9801234568',email='another@example.com').data['id']
        self.client.post(f'/api/v1/people/{second}/followups/',{'subject':'Call','due_at':timezone.now().isoformat(),'method':'CALL'},format='json')
        response=self.client.post('/api/v1/merges/',{'survivor_id':first,'merged_id':second,'reason':'Human review confirmed'},format='json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(response.status_code,200,response.data)
        self.assertTrue(Person.objects.get(pk=second).archived_at)
        self.assertEqual(ContactMethod.objects.filter(person_id=first).count(),4)
        self.assertEqual(FollowUp.objects.filter(person_id=first).count(),1)
        self.assertEqual(Activity.objects.filter(person_id=second).count(),0)
    def test_round_robin_skips_leave_and_overload(self):
        AssignmentRule.objects.create(branch=self.branch,mode='ROUND_ROBIN')
        self.owner.staff.availability='ON_LEAVE';self.owner.staff.save()
        with transaction.atomic():self.assertEqual(automatic_owner(self.branch),self.other)
        self.other.staff.availability='INACTIVE';self.other.staff.save()
        with transaction.atomic():self.assertIsNone(automatic_owner(self.branch))
    def test_store_first_unverified_no_person_until_valid_code(self):
        result=self.submit()
        self.assertEqual(Person.objects.count(),0)
        self.assertEqual(IntakeSubmission.objects.get().status,'UNVERIFIED')
        self.assertNotEqual(OtpChallenge.objects.get().code_hash,self.provider.codes[-1])
        response=self.public.post(f'/api/public/intake/submissions/{result["id"]}/otp/verify/',{'code':self.provider.codes[-1]},format='json',HTTP_X_RESUME_TOKEN=result['resume_token'])
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(Person.objects.count(),1)
        self.assertEqual(ContactMethod.objects.get(type='PHONE').verified_via,'OTP_SMS')
    def test_provider_failure_saved_and_manager_only_queue(self):
        with patch('intake.services.get_provider') as mock:
            mock.return_value.name='failure';mock.return_value.send.side_effect=RuntimeError('unavailable')
            response=self.public.post('/api/public/intake/submissions/',{'code':self.qr.code,'full_name':'Visitor','phone':'9801234599','consent':True,'turnstile_token':'development'},format='json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(response.status_code,201,response.data);self.assertTrue(response.data['send_failed']);self.assertEqual(Person.objects.count(),0)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get('/api/v1/intake/submissions/').status_code,403)
        self.client.force_authenticate(self.manager)
        response=self.client.post(f'/api/v1/intake/submissions/{IntakeSubmission.objects.get().pk}/verify-by-call/',{'reason':'Confirmed number by call'},format='json')
        self.assertEqual(response.status_code,200,response.data);self.assertEqual(Person.objects.count(),1)
    def test_five_wrong_codes_lock_and_never_create_person(self):
        result=self.submit()
        bad='000000' if self.provider.codes[-1]!='000000' else '111111'
        for i in range(5):
            response=self.public.post(f'/api/public/intake/submissions/{result["id"]}/otp/verify/',{'code':bad},format='json',HTTP_X_RESUME_TOKEN=result['resume_token'])
            self.assertEqual(response.status_code,400);self.assertEqual(response.data['attempts_left'],4-i)
        self.assertEqual(OtpChallenge.objects.get().status,'LOCKED');self.assertEqual(Person.objects.count(),0)
    def test_public_replay_and_honeypot_and_missing_turnstile(self):
        key=str(uuid.uuid4());data={'code':self.qr.code,'full_name':'Visitor','phone':'9801234599','consent':True,'turnstile_token':'development'}
        with patch('intake.services.get_provider',return_value=self.provider):
            a=self.public.post('/api/public/intake/submissions/',data,format='json',HTTP_IDEMPOTENCY_KEY=key)
            b=self.public.post('/api/public/intake/submissions/',data,format='json',HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(a.data['id'],b.data['id']);self.assertEqual(len(self.provider.codes),1)
        data['website']='spam';self.assertEqual(self.public.post('/api/public/intake/submissions/',data,format='json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())).status_code,400)
        data.pop('website');data.pop('turnstile_token');self.assertEqual(self.public.post('/api/public/intake/submissions/',data,format='json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())).status_code,400)
    def test_qr_safety_styles_payload_and_immutable_code(self):
        for module in ['square','rounded','dots','extra-rounded','diamond']:
            self.qr.design={'module':module,'logo':False};png,svg,text,_=render(self.qr);self.assertIn(self.qr.code,text);self.assertTrue(png);self.assertIn(b'<svg',svg)
        with self.assertRaises(ValidationError):safe_design({'foreground':'#ffffff','background':'#000000'})
        with self.assertRaises(ValidationError):safe_design({'logo_fraction':.8})
        old=self.qr.code;create_asset(self.qr)
        response=self.client.patch(f'/api/v1/qr/{self.qr.pk}/',{'design':{'module':'rounded','logo':False}},format='json')
        self.assertEqual(response.status_code,200,response.data);self.qr.refresh_from_db();self.assertEqual(old,self.qr.code);self.assertEqual(self.qr.asset_version,2)
    def test_import_lifecycle_idempotence_and_rollback(self):
        file=SimpleUploadedFile('people.csv',b'Name,Phone,Email\nImported Example,9801234588,imported@example.com\n')
        response=self.client.post('/api/v1/imports/',{'file':file},format='multipart')
        self.assertEqual(response.status_code,201,response.data);batch_id=response.data['id']
        response=self.client.post(f'/api/v1/imports/{batch_id}/validate/',{'mapping':{'full_name':'Name','phone':'Phone','email':'Email'},'defaults':{'owner_id':self.owner.pk,'source_id':self.source.pk,'consent':True}},format='json')
        self.assertEqual(response.status_code,200,response.data);self.assertEqual(response.data['counts']['valid'],1)
        self.client.post(f'/api/v1/imports/{batch_id}/approve/',{},format='json')
        key=str(uuid.uuid4())
        for _ in range(2):
            response=self.client.post(f'/api/v1/imports/{batch_id}/commit/',{},format='json',HTTP_IDEMPOTENCY_KEY=key);self.assertEqual(response.status_code,200,response.data)
        person=Person.objects.get();self.assertEqual(person.source_file,'people.csv');self.assertEqual(person.import_row_number,2)
        response=self.client.post(f'/api/v1/imports/{batch_id}/rollback/',{'reason':'Reviewed import rollback'},format='json')
        self.assertEqual(response.status_code,200,response.data);person.refresh_from_db();self.assertTrue(person.archived_at)
    def test_bulk_status_and_export_audit(self):
        person=self.create().data['id']
        response=self.client.post('/api/v1/bulk/',{'ids':[person],'operation':'status','status':'CONTACTED','reason':'Bulk triage'},format='json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(response.status_code,200,response.data)
        response=self.client.get('/api/v1/export/')
        self.assertEqual(response.status_code,200);self.assertNotIn(b'guardian',response.content);self.assertTrue(AuditEvent.objects.filter(action='PEOPLE_EXPORTED').exists())

    def test_resend_idempotence_cooldown_and_expiry(self):
        saved=self.submit();sub=IntakeSubmission.objects.get(pk=saved['id'])
        headers={'HTTP_X_RESUME_TOKEN':saved['resume_token'],'HTTP_IDEMPOTENCY_KEY':str(uuid.uuid4())}
        OtpChallenge.objects.filter(submission=sub).update(last_sent_at=timezone.now()-timedelta(seconds=61))
        with patch('intake.services.get_provider',return_value=self.provider):
            for _ in range(2):
                result=self.public.post(f'/api/public/intake/submissions/{sub.pk}/otp/resend/',{},format='json',**headers)
                self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(len(self.provider.codes),2)
        sub.refresh_from_db();self.assertEqual(sub.send_count,2)
        result=self.public.post(f'/api/public/intake/submissions/{sub.pk}/otp/resend/',{},format='json',HTTP_X_RESUME_TOKEN=saved['resume_token'])
        self.assertEqual(result.status_code,429)
        OtpChallenge.objects.filter(submission=sub,status='PENDING').update(expires_at=timezone.now()-timedelta(seconds=1))
        result=self.public.post(f'/api/public/intake/submissions/{sub.pk}/otp/verify/',{'code':self.provider.codes[-1]},format='json',HTTP_X_RESUME_TOKEN=saved['resume_token'])
        self.assertEqual(result.status_code,400);self.assertEqual(Person.objects.count(),0)

    def test_qr_all_types_frames_and_inactive_intake(self):
        for kind,content in [('REGISTRATION',{}),('WHATSAPP',{'phone':'9801234599','message':'Hello'}),('URL',{'url':'https://example.com/study'}),('VCARD',{'name':'Example','phone':'+9779801234599'})]:
            self.qr.content_type=kind;self.qr.content=content
            for frame in ['none','border','caption','poster']:
                self.qr.design={'logo':False,'frame':frame};png,svg,payload,_=render(self.qr);self.assertTrue(png);self.assertTrue(payload);self.assertTrue(svg)
        self.qr.status='PAUSED';self.qr.save()
        result=self.public.post('/api/public/intake/submissions/',{'code':self.qr.code,'full_name':'Inactive visitor','phone':'9801234599','consent':True,'turnstile_token':'development'},format='json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(result.status_code,400);self.assertFalse(IntakeSubmission.objects.exists())

    def test_import_review_override_and_edited_rollback_block(self):
        original=self.create().data['id']
        file=SimpleUploadedFile('duplicates.csv',b'Name,Phone\nDistinct family member,9801234567\n')
        response=self.client.post('/api/v1/imports/',{'file':file},format='multipart');batch_id=response.data['id']
        self.client.post(f'/api/v1/imports/{batch_id}/validate/',{'mapping':{'full_name':'Name','phone':'Phone'},'defaults':{'owner_id':self.owner.pk,'source_id':self.source.pk,'consent':True}},format='json')
        batch=ImportBatch.objects.get(pk=batch_id);row=batch.rows.get();self.assertEqual(row.result,'DUPLICATE')
        self.assertEqual(self.client.post(f'/api/v1/imports/{batch_id}/approve/',{},format='json').status_code,400)
        response=self.client.post(f'/api/v1/imports/{batch_id}/review/',{'row_id':row.pk,'decision':'CREATE','reason':'Reviewed shared family number'},format='json');self.assertEqual(response.status_code,200,response.data)
        self.client.post(f'/api/v1/imports/{batch_id}/approve/',{},format='json')
        response=self.client.post(f'/api/v1/imports/{batch_id}/commit/',{},format='json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()));self.assertEqual(response.status_code,200,response.data)
        row.refresh_from_db();self.assertTrue(row.person_id);self.assertNotEqual(str(row.person_id),original)
        self.client.patch(f'/api/v1/people/{row.person_id}/profile/',{'address':'Edited after import'},format='json')
        response=self.client.post(f'/api/v1/imports/{batch_id}/rollback/',{'reason':'Attempt rollback'},format='json')
        self.assertEqual(response.status_code,400);row.person.refresh_from_db();self.assertFalse(row.person.archived_at)

    def test_staff_assisted_requires_consent_and_captures_temperature(self):
        values={'code':self.qr.code,'full_name':'Assisted visitor','phone':'9801234598','temperature':'HOT','summary':'Discussed study plans','next_step':'Follow up','next_due_at':(timezone.now()+timedelta(days=1)).isoformat()}
        self.assertEqual(self.client.post('/api/v1/intake/assisted/',values,format='json').status_code,400)
        result=self.client.post('/api/v1/intake/assisted/',{**values,'consent':True},format='json');self.assertEqual(result.status_code,201,result.data)
        person=Person.objects.get(pk=result.data['person_id']);self.assertEqual(person.temperature,'HOT');self.assertTrue(person.activities.filter(type='MEETING').exists());self.assertTrue(person.followups.filter(subject='Follow up').exists())

    def test_calendar_change_recomputes_sla_and_reports_are_scoped(self):
        person_id=self.create().data['id'];person=Person.objects.get(pk=person_id)
        timer=person.sla_timers.filter(rule__trigger='FIRST_CONTACT').first();old=timer.due_at
        from .calendar import recompute_calendar
        self.calendar.close_time=__import__('datetime').time(12,0);self.calendar.save();recompute_calendar(self.calendar)
        timer.refresh_from_db();self.assertGreater(timer.due_at,old)
        response=self.client.get('/api/v1/reports/');self.assertEqual(response.status_code,200);self.assertEqual(response.data['people'],1)
        self.client.force_authenticate(self.user('other_manager','MANAGER',self.other_branch));self.assertEqual(self.client.get('/api/v1/reports/').data['people'],0)

    def test_direct_endpoint_permission_matrix_every_role_action(self):
        tag=Tag.objects.create(name='Permission fixture')
        for role,permissions in MATRIX.items():
            user=self.user('endpoint_'+role,role,self.branch)
            person=Person.objects.create(ref=__import__('crm.services',fromlist=['next_reference']).next_reference(),full_name='Permission fixture '+role,owner=user if role=='COUNSELOR' else self.owner,branch=self.branch,source=self.source)
            if role=='DOCS':TeamMember.objects.create(person=person,user=user)
            calls={
                'view':('get','people/',None),
                'create':('post','people/',self.payload(phone='9801234511',email='',owner_id=user.pk if role=='COUNSELOR' else self.owner.pk)),
                'edit':('patch',f'people/{person.pk}/profile/',{'highest_education':'Bachelor'}),
                'work':('post','tasks/',{'person_id':str(person.pk),'title':'Permission task','due_at':(timezone.now()+timedelta(days=1)).isoformat()}),
                'convert':('post',f'people/{person.pk}/convert/',{}),
                'request_access':('post','access-requests/',{'person_id':str(person.pk),'reason':'Reviewed access'}),
                'reassign':('post',f'people/{person.pk}/reassign/',{'owner_id':self.owner.pk,'reason':'Reviewed assignment'}),
                'bulk':('post','bulk/',{'ids':[str(person.pk)],'operation':'tag','tag_id':tag.pk,'reason':'Reviewed bulk'}),
                'intake':('get','intake/submissions/',None),'qr':('get','qr/',None),
                'review':('get','duplicate-review/',None),'merge':('get','merges/',None),
                'import':('get','imports/',None),'export':('get','export/',None),'audit':('get','audit/',None),
                'settings':('post','settings/',{'kind':'tags','data':{'name':'New permission tag'}}),
                'archive':('post',f'people/{person.pk}/archive/',{'reason':'Reviewed archive'}),
                'discovery':('get','discovery/?search=Permission',None),
            }
            if role=='FRONTDESK':calls['create']=('post','intake/paper/',{'code':self.qr.code,'full_name':'Paper permission fixture','phone':'9801234511','channel':'WHATSAPP','consent':True})
            self.client.force_authenticate(user)
            for action,(method,path,data) in calls.items():
                with self.subTest(role=role,action=action),transaction.atomic():
                    response=getattr(self.client,method)('/api/v1/'+path,**({'data':data,'format':'json','HTTP_IDEMPOTENCY_KEY':str(uuid.uuid4())} if data is not None else {}))
                    if action in permissions:self.assertLess(response.status_code,400,getattr(response,'data',None))
                    else:self.assertIn(response.status_code,[403,404],getattr(response,'data',None))
                    transaction.set_rollback(True)

    def test_spend_cap_saves_submission_alerts_manager_and_never_sends(self):
        SystemPolicy.objects.create(key='otp_monthly_budget_minor',value=0)
        with override_settings(SMS_MESSAGE_COST_MINOR=1):
            saved=self.submit()
        self.assertTrue(saved['send_failed']);self.assertEqual(len(self.provider.codes),0);self.assertEqual(Person.objects.count(),0)
        self.assertTrue(Notification.objects.filter(recipient=self.manager,type='OTP_LIMIT').exists())

    def test_merge_reverse_preserves_arrays_and_grant_history(self):
        a=Person.objects.get(pk=self.create(preferred_countries=['Australia']).data['id']);b=Person.objects.get(pk=self.create(full_name='Reviewed second',phone='9801234568',email='',preferred_countries=['Canada']).data['id'])
        AccessGrant.objects.create(person=a,user=self.other,active=False);AccessGrant.objects.create(person=b,user=self.other,active=True)
        result=self.client.post('/api/v1/merges/',{'survivor_id':str(a.pk),'merged_id':str(b.pk),'reason':'Reviewed merge','field_choices':{'preferred_countries':'merged'}},format='json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()));self.assertEqual(result.status_code,200,result.data)
        admin=self.user('reverse_admin','ADMIN',self.branch);self.client.force_authenticate(admin)
        result=self.client.post(f"/api/v1/merges/{result.data['merge_id']}/reverse/",{'reason':'Reviewed reversal'},format='json');self.assertEqual(result.status_code,200,result.data)
        a.refresh_from_db();b.refresh_from_db();self.assertEqual(a.preferred_countries,['Australia']);self.assertFalse(b.archived_at);self.assertFalse(AccessGrant.objects.get(person=a,user=self.other).active);self.assertTrue(AccessGrant.objects.get(person=b,user=self.other).active)

    def test_qr_downloads_final_svg_png_pdf_and_safe_svg_logo(self):
        import base64
        logo=b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect width="80" height="80" fill="#1e3a8a"/><circle cx="40" cy="40" r="20" fill="#ffffff"/></svg>'
        self.qr.design={'logo_data':base64.b64encode(logo).decode(),'frame':'caption'};create_asset(self.qr)
        for fmt in ['png','svg','pdf']:
            layouts=['a4','a5','tent'] if fmt=='pdf' else ['a4']
            for layout in layouts:
                result=self.client.get(f'/api/v1/qr/{self.qr.pk}/download/?format={fmt}&layout={layout}');self.assertEqual(result.status_code,200,getattr(result,'data',None));self.assertTrue(result.content)
        bad=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        with self.assertRaises(ValidationError):safe_design({'logo_data':base64.b64encode(bad).decode()})

    def test_rolling_limits_and_proxy_ip_are_enforced(self):
        from intake.services import consume
        from intake.network import client_ip
        from intake.models import RateEvent
        from types import SimpleNamespace
        from rest_framework.exceptions import Throttled
        with transaction.atomic():
            consume('test-window',2);consume('test-window',2)
        with self.assertRaises(Throttled),transaction.atomic():consume('test-window',2)
        RateEvent.objects.filter(key='test-window:rolling-24h').update(at=timezone.now()-timedelta(hours=25))
        with transaction.atomic():consume('test-window',2)
        with override_settings(TRUSTED_PROXY_IPS=['127.0.0.1/32']):
            self.assertEqual(client_ip(SimpleNamespace(META={'REMOTE_ADDR':'198.51.100.1'},headers={'X-Forwarded-For':'203.0.113.1'})),'198.51.100.1')
            self.assertEqual(client_ip(SimpleNamespace(META={'REMOTE_ADDR':'127.0.0.1'},headers={'X-Forwarded-For':'203.0.113.1, 198.51.100.1'})),'198.51.100.1')

    def test_name_transliteration_order_and_similarity_are_review_signals(self):
        person=Person.objects.get(pk=self.create(full_name='Dr. José Sharma',phone='9801234567',email='',dob='2000-01-02',preferred_country='Canada').data['id'])
        matches=duplicate_signals(name='Sharma Jose',dob='2000-01-02')
        match=next(m for m in matches if m['person'].pk==person.pk)
        self.assertEqual(match['confidence'],'HIGH');self.assertIn('DOB',match['signals'])
        match=next(m for m in duplicate_signals(name='Jose Sharmma',country='Canada') if m['person'].pk==person.pk)
        self.assertEqual(match['confidence'],'MEDIUM');self.assertIn('NAME_SIMILARITY',match['signals'])
        self.assertEqual(Person.objects.count(),1)

    def test_import_conflict_after_approval_can_be_reviewed_and_recommitted(self):
        file=SimpleUploadedFile('race.csv',b'Name,Phone\nReviewed visitor,9801234567\n')
        batch_id=self.client.post('/api/v1/imports/',{'file':file},format='multipart').data['id']
        url=f'/api/v1/imports/{batch_id}/'
        self.client.post(url+'validate/',{'mapping':{'full_name':'Name','phone':'Phone'},'defaults':{'owner_id':self.owner.pk,'source_id':self.source.pk,'consent':True}},format='json')
        self.client.post(url+'approve/',{},format='json')
        self.create()
        result=self.client.post(url+'commit/',{},format='json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(result.status_code,200);self.assertEqual(result.data['status'],'VALIDATED')
        row=ImportBatch.objects.get(pk=batch_id).rows.get();self.assertEqual(row.result,'NEEDS_REVIEW');self.assertIsNone(row.person_id)
        self.client.post(url+'review/',{'row_id':row.pk,'decision':'CREATE','reason':'Verified separate family member'},format='json')
        self.client.post(url+'approve/',{},format='json')
        result=self.client.post(url+'commit/',{},format='json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(result.data['status'],'COMMITTED');self.assertEqual(result.data['counts']['committed'],1)

    def test_permission_changes_audit_previous_and_resulting_roles_without_passwords(self):
        admin=self.user('settings_admin','ADMIN',self.branch);target=self.user('settings_target','DOCS',self.branch)
        self.client.force_authenticate(admin)
        response=self.client.patch('/api/v1/settings/',{'kind':'user','id':target.pk,'data':{'role':'MANAGEMENT'}},format='json')
        self.assertEqual(response.status_code,200,response.data)
        event=AuditEvent.objects.get(action='USER_PERMISSION_CHANGED',object_id=str(target.pk))
        self.assertEqual(event.old['role'],'DOCS');self.assertEqual(event.new['role'],'MANAGEMENT');self.assertNotIn('password',event.new)

    def test_retention_clears_unverified_details_and_challenges_without_creating_person(self):
        from django.core.management import call_command
        saved=self.submit();submission=IntakeSubmission.objects.get(pk=saved['id'])
        IntakeSubmission.objects.filter(pk=submission.pk).update(purge_after=timezone.now()-timedelta(days=1))
        call_command('run_housekeeping',stdout=io.StringIO())
        submission.refresh_from_db();self.assertEqual(submission.status,'PURGED');self.assertEqual(submission.payload,{});self.assertFalse(submission.resume_token_hash)
        challenge=submission.challenges.get();self.assertFalse(challenge.code_hash);self.assertFalse(challenge.phone_e164);self.assertEqual(Person.objects.count(),0)
