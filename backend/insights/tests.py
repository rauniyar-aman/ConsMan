from django.contrib.auth.models import User
from django.db import transaction, DatabaseError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from crm.models import Branch, Source, Person, StaffProfile, FollowUp
from admissions.models import Document, Blocker, Deadline
from .models import AssistanceReview
from unittest.mock import patch, Mock
import json
from . import provider


class InsightTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name='Insight office',code='INSIGHT')
        source = Source.objects.create(name='Insight source')
        self.user = User.objects.create_user(username='insight-owner')
        StaffProfile.objects.create(user=self.user,role='COUNSELOR',branch=self.branch)
        self.person = Person.objects.create(ref='BE-INSIGHT-1',full_name='Evidence Student',branch=self.branch,source=source,owner=self.user)
        self.client = APIClient(); self.client.force_authenticate(self.user)
        self.path = f'/api/v1/insights/people/{self.person.pk}/'

    def test_evidence_missing_documents_expiry_and_priority(self):
        Document.objects.create(person=self.person,title='Passport',type='PASSPORT',status='VERIFIED',expires_at=timezone.now()-timezone.timedelta(days=1))
        Blocker.objects.create(person=self.person,type='DOCUMENTS',description='Missing evidence',assigned_to=self.user,created_by=self.user)
        FollowUp.objects.create(person=self.person,owner=self.user,subject='Call student',due_at=timezone.now()-timezone.timedelta(days=2))
        data = self.client.get(self.path).data
        self.assertEqual(data['priority']['health'],'BLOCKED')
        self.assertEqual(data['documents'][0]['status'],'EXPIRED')
        self.assertIn('Passport',data['draft'])
        self.assertTrue(all(r['id'] for r in data['priority']['reasons']))

    def test_scope_and_management_read_only(self):
        other = User.objects.create_user(username='outside-owner')
        StaffProfile.objects.create(user=other,role='COUNSELOR',branch=self.branch)
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get(self.path).status_code,404)
        self.assertEqual(self.client.get('/api/v1/insights/workspace/').data['count'],0)
        other.staff.role='MANAGEMENT';other.staff.save()
        self.assertEqual(self.client.get(self.path).status_code,200)
        self.assertEqual(self.client.post(self.path,{'kind':'SUMMARY','decision':'ACCEPTED','reason':'Reviewed'},format='json').status_code,403)

    def test_review_snapshot_immutable_and_no_case_changes(self):
        r = self.client.post(self.path,{'kind':'SUMMARY','decision':'ACCEPTED','reason':'Checked source records'},format='json')
        self.assertEqual(r.status_code,201,r.data)
        review = AssistanceReview.objects.get()
        self.assertIn('summary',review.evidence)
        self.person.refresh_from_db();self.assertEqual(self.person.lead_status,'NEW')
        with self.assertRaises(DatabaseError),transaction.atomic():
            AssistanceReview.objects.filter(pk=review.pk).update(note='Changed history')

    def test_analytics_and_anomalies_are_scoped(self):
        self.person.owner=None;self.person.save()
        manager=User.objects.create_user(username='insight-manager');StaffProfile.objects.create(user=manager,role='MANAGER',branch=self.branch)
        self.client.force_authenticate(manager)
        result=self.client.get('/api/v1/insights/workspace/').data
        self.assertEqual(result['count'],1)
        self.assertEqual(result['anomalies'][0]['count'],1)
        self.assertEqual(result['pipeline'][0]['count'],1)

    def test_cancelled_followups_not_priority(self):
        self.person.next_action_due_at=timezone.now()+timezone.timedelta(days=1);self.person.save()
        FollowUp.objects.create(person=self.person,owner=self.user,subject='Cancelled',status='CANCELLED',due_at=timezone.now()-timezone.timedelta(days=1))
        self.assertEqual(self.client.get(self.path).data['priority']['score'],0)

    def test_optional_provider_disabled_by_default(self):
        with patch.dict('os.environ', {'ASSISTANCE_DAILY_REQUEST_LIMIT':'0'}), patch('insights.provider.requests.post') as send:
            self.assertEqual(self.client.post(self.path+'generate/',{'reason':'Preview wording'},format='json').status_code,400)
            send.assert_not_called()

    def provider_response(self, result):
        response=Mock(status_code=200)
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.iter_content.return_value=[json.dumps({'choices':[{'message':{'content':json.dumps(result)}}]}).encode()]
        return response

    def test_provider_anonymized_evidence_and_request_cap(self):
        env={'ASSISTANCE_SERVICE_URL':'https://text-service.invalid/completions','ASSISTANCE_SERVICE_KEY':'test-key','ASSISTANCE_MODEL':'test-model','ASSISTANCE_DAILY_REQUEST_LIMIT':'1'}
        with patch.dict('os.environ',env),patch('insights.provider.requests.post',return_value=self.provider_response({'summary':'A lead has no next action.','draft':'Hello Student, please contact the office.','citations':['PROFILE']})) as send:
            r=self.client.post(self.path+'generate/',{'reason':'Review a draft'},format='json')
            self.assertEqual(r.status_code,201,r.data)
            self.assertNotIn(self.person.full_name,json.dumps(send.call_args.kwargs['json']))
            self.assertNotIn(self.person.ref,json.dumps(send.call_args.kwargs['json']))
            self.assertFalse(send.call_args.kwargs['allow_redirects'])
            self.assertEqual(self.client.post(self.path+'generate/',{'reason':'Second request'},format='json').status_code,400)
            self.assertEqual(send.call_count,1)
            r=self.client.post(self.path,{'kind':'DRAFT','decision':'ACCEPTED','reason':'Confirmed evidence','generation_id':r.data['id']},format='json')
            self.assertEqual(r.status_code,201,r.data)
            self.assertIn('provider_generation',AssistanceReview.objects.get().evidence)

    def test_provider_unknown_sources_rejected(self):
        env={'ASSISTANCE_SERVICE_URL':'https://text-service.invalid/completions','ASSISTANCE_SERVICE_KEY':'test-key','ASSISTANCE_MODEL':'test-model','ASSISTANCE_DAILY_REQUEST_LIMIT':'1'}
        with patch.dict('os.environ',env),patch('insights.provider.requests.post',return_value=self.provider_response({'summary':'Unsupported','draft':'Unsupported','citations':['NOT-A-SOURCE']})):
            self.assertEqual(self.client.post(self.path+'generate/',{'reason':'Review wording'},format='json').status_code,400)

    def test_queue_ranks_incomplete_documents_across_scope(self):
        other=Person.objects.create(ref='BE-INSIGHT-2',full_name='Missing documents',branch=self.branch,source=self.person.source,owner=self.user)
        for i in range(5):Document.objects.create(person=other,title=f'Missing {i}',type=f'DOC-{i}')
        self.assertEqual(self.client.get('/api/v1/insights/workspace/').data['priorities'][0]['id'],str(other.pk))
