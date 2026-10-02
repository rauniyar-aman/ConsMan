from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from crm.models import Branch, Person, Source, StaffProfile, ContactMethod
from .models import MessageTemplate, OutboundMessage
from .services import eligible
from unittest.mock import patch,Mock
from django.test import override_settings
from django.db import transaction,DatabaseError
from datetime import timedelta
import os,json,hashlib,hmac
from .models import ChannelConsent,MessageEvent,InboundMessage,WebhookReceipt,AutomationRule,AutomationRun,LeadForm,SocialLead
from .services import queue
from .dispatch import deliver,sweep
from .providers import SendError,send
from .webhooks import process,fetch_lead
from .automation import run_rule
from crm.models import FollowUp,Notification


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
        message=OutboundMessage.objects.get();self.assertTrue(message.body.startswith('Hello Test Student at Office'));self.assertTrue(eligible(message))
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

    def queued(self,channel='EMAIL'):
        template=MessageTemplate.objects.create(name='Dispatch '+str(MessageTemplate.objects.count()),channel=channel,body='Hello {{student_name}}',created_by=self.admin)
        contact=self.contact
        if channel!='EMAIL':contact=ContactMethod.objects.create(person=self.person,type='PHONE',raw_value='+9779860000001',normalized_value='+9779860000001',verified_at=timezone.now())
        ChannelConsent.objects.create(person=self.person,channel=channel,allowed=True,evidence='Test permission',recorded_by=self.admin)
        return queue(self.person,contact,template,self.admin)

    @patch('communications.dispatch.configured',return_value=True)
    @patch('communications.dispatch.send',return_value='provider-id')
    def test_delivery_idempotence_and_revocation(self,send_mock,configured):
        obj=self.queued();self.assertEqual(deliver(obj.pk),'SENT');self.assertEqual(deliver(obj.pk),'SENT');send_mock.assert_called_once()
        obj=self.queued();ChannelConsent.objects.create(person=self.person,channel='EMAIL',allowed=False,evidence='Revoked',recorded_by=self.admin)
        self.assertEqual(deliver(obj.pk),'CANCELLED');self.assertEqual(send_mock.call_count,1)

    @patch('communications.dispatch.configured',return_value=True)
    @patch('communications.dispatch.send')
    def test_uncertain_send_never_automatically_retries(self,send_mock,configured):
        obj=self.queued();send_mock.side_effect=SendError('TIMEOUT',uncertain=True)
        self.assertEqual(deliver(obj.pk),'UNKNOWN');self.assertNotIn(obj.pk,sweep());self.assertEqual(deliver(obj.pk),'UNKNOWN');self.assertEqual(send_mock.call_count,1)
        self.assertEqual(self.post({'action':'retry','message_id':str(obj.pk),'reason':'Do not retry uncertain sends'}).status_code,400)
        r=self.client.post('/api/v1/communications/workspace/',{'action':'message_reconcile','id':str(obj.pk),'status':'FAILED','reason':'Provider confirms message was not accepted'},format='json');self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(self.post({'action':'retry','message_id':str(obj.pk),'reason':'Confirmed not delivered'}).status_code,200)

    @patch('communications.dispatch.configured',return_value=True)
    @patch('communications.dispatch.send',side_effect=SendError('RATE_LIMIT',retryable=True))
    def test_safe_retry_backoff_stale_claim_and_limit(self,send_mock,configured):
        obj=self.queued();self.assertEqual(deliver(obj.pk),'QUEUED');obj.refresh_from_db();self.assertGreater(obj.scheduled_at,timezone.now());self.assertEqual(obj.attempts,1)
        obj.status='SENDING';obj.claimed_at=timezone.now()-timedelta(minutes=10);obj.save();self.assertNotIn(obj.pk,sweep());obj.refresh_from_db();self.assertEqual(obj.status,'UNKNOWN')
        other=self.queued()
        with override_settings(COMMUNICATION_HOURLY_LIMIT=0):self.assertEqual(deliver(other.pk),'HOURLY_LIMIT')

    def signed(self,provider,payload,secret='test-webhook',signature=True):
        raw=json.dumps(payload).encode();headers={'HTTP_X_WEBHOOK_SIGNATURE':'sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest() if signature else 'invalid'}
        if provider=='meta':headers={'HTTP_X_HUB_SIGNATURE_256':headers['HTTP_X_WEBHOOK_SIGNATURE']}
        return self.client.post('/api/v1/communications/webhooks/'+provider+'/',raw,content_type='application/json',**headers)

    @override_settings(WHATSAPP_GATEWAY_WEBHOOK_SECRET='test-webhook',WHATSAPP_GATEWAY_SESSION='test-session')
    def test_gateway_two_way_binding_status_monotonic_and_stop(self):
        obj=self.queued('WHATSAPP');obj.status='UNKNOWN';obj.save()
        sent={'sessionId':'test-session','event':'message.sent','data':{'key':{'id':'abc','remoteJid':'9779860000001@s.whatsapp.net'},'content':obj.body}}
        self.assertEqual(self.signed('gateway',sent,signature=False).status_code,403)
        self.assertEqual(self.signed('gateway',sent).status_code,202);self.assertTrue(self.signed('gateway',sent).data['duplicate'])
        process(WebhookReceipt.objects.get().pk);obj.refresh_from_db();self.assertEqual(obj.provider_id,'abc');self.assertEqual(obj.status,'SENT')
        for status in ['READ','DELIVERED','FAILED']:
            payload={'sessionId':'test-session','event':'message.status','data':{'keyId':'abc','status':status}}
            self.signed('gateway',payload);process(WebhookReceipt.objects.order_by('-pk').first().pk)
        obj.refresh_from_db();self.assertEqual(obj.status,'READ')
        self.signed('gateway',{'sessionId':'test-session','event':'message.received','data':{'key':{'id':'incoming'},'from':'9779860000001@s.whatsapp.net','content':'STOP'}});process(WebhookReceipt.objects.order_by('-pk').first().pk)
        self.assertEqual(InboundMessage.objects.get().person,self.person);self.assertFalse(eligible(obj))
        self.assertEqual(self.client.get(self.path).data['incoming'][0]['body'],'STOP')

    @patch.dict(os.environ,{'EMAIL_WEBHOOK_SECRET':'test-webhook'})
    def test_shared_incoming_requires_review_and_scope(self):
        ContactMethod.objects.create(person=Person.objects.create(ref='BE-OTHER',full_name='Another Student',branch=self.branch,source=self.person.source),type='EMAIL',raw_value=self.contact.raw_value,normalized_value=self.contact.normalized_value,verified_at=timezone.now(),is_shared=True)
        self.signed('email',{'event':'inbound','id':'email-in','from':self.contact.raw_value,'body':'Please help'});process(WebhookReceipt.objects.get().pk)
        incoming=InboundMessage.objects.get();self.assertIsNone(incoming.person_id)
        r=self.client.post('/api/v1/communications/workspace/',{'action':'inbound_match','id':str(incoming.pk),'person_id':str(self.person.pk),'reason':'Verified sender identity'},format='json');self.assertEqual(r.status_code,201,r.data)
        self.client.force_authenticate(self.other);self.assertEqual(self.client.get('/api/v1/communications/workspace/').status_code,403)

    def test_automation_deduplication_consent_and_disabled_rules(self):
        obj=self.queued();rule=AutomationRule.objects.create(name='Due',branch=self.branch,trigger='FOLLOWUP_DUE',template=obj.template,student_messages=True,staff_notifications=True,advance_minutes=60,starts_at=timezone.now()-timedelta(minutes=1),created_by=self.admin)
        FollowUp.objects.create(person=self.person,owner=self.admin,subject='Call student',due_at=timezone.now()+timedelta(minutes=10))
        run_rule(rule.pk);self.assertFalse(AutomationRun.objects.exists())
        rule.enabled=True;rule.save();run_rule(rule.pk);run_rule(rule.pk)
        self.assertEqual(AutomationRun.objects.count(),1);self.assertEqual(Notification.objects.filter(type='AUTOMATION').count(),1);self.assertEqual(OutboundMessage.objects.count(),2)
        self.assertIn('STUDENT_QUEUED',AutomationRun.objects.get().outcome)

    @patch.dict(os.environ,{'META_APP_SECRET':'test-webhook','META_VERIFY_TOKEN':'test-verify','META_PAGE_ACCESS_TOKEN':'test-token','META_GRAPH_VERSION':'v23.0'})
    @patch('communications.webhooks.requests.get')
    def test_social_lead_staging_fetch_review_and_no_implicit_consent(self,get):
        form=LeadForm.objects.create(form_id='100',page_id='200',platform='INSTAGRAM',name='Office',branch=self.branch,source=self.person.source,enabled=True,created_by=self.admin)
        r=self.client.get('/api/v1/communications/webhooks/meta/',{'hub.mode':'subscribe','hub.verify_token':'test-verify','hub.challenge':'challenge'});self.assertEqual(r.content,b'challenge')
        payload={'entry':[{'id':'200','changes':[{'field':'leadgen','value':{'form_id':'100','leadgen_id':'300','page_id':'200'}}]}]}
        self.assertEqual(self.signed('meta',payload).status_code,200);process(WebhookReceipt.objects.get().pk)
        lead=SocialLead.objects.get();self.assertEqual(lead.status,'PENDING')
        get.return_value=Mock(ok=True,json=lambda:{'id':'300','form_id':'100','field_data':[{'name':'full_name','values':['Unique Social Lead']},{'name':'email','values':['unique-lead@example.test']}]})
        fetch_lead(lead.pk);lead.refresh_from_db();self.assertEqual(lead.status,'REVIEW')
        r=self.client.post('/api/v1/communications/workspace/',{'action':'lead_review','id':lead.pk,'reason':'Reviewed genuine lead'},format='json');self.assertEqual(r.status_code,201,r.data)
        lead.refresh_from_db();self.assertEqual(lead.status,'ACCEPTED');self.assertFalse(ChannelConsent.objects.filter(person=lead.person).exists());self.assertIsNone(lead.person.contacts.get().verified_at)
        self.assertEqual(self.client.post('/api/v1/communications/workspace/',{'action':'lead_review','id':lead.pk,'reason':'Replay'},format='json').status_code,400)

    def test_database_history_guards(self):
        obj=self.queued()
        for model in [MessageTemplate,ChannelConsent,MessageEvent]:
            with self.assertRaises(DatabaseError):
                with transaction.atomic():model.objects.all().delete()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():OutboundMessage.objects.filter(pk=obj.pk).update(body='tampered')

    @override_settings(WHATSAPP_GATEWAY_URL='https://gateway.example.test',WHATSAPP_GATEWAY_KEY='key',WHATSAPP_GATEWAY_SESSION='session',SMS_PROVIDER_URL='https://sms.example.test',SMS_PROVIDER_KEY='key')
    @patch('communications.providers.requests.post')
    def test_provider_contract_and_ambiguous_failures(self,post):
        obj=self.queued('WHATSAPP');post.return_value=Mock(ok=True,status_code=200,json=lambda:{'status':True})
        self.assertEqual(send(obj),'');self.assertEqual(post.call_args.kwargs['json']['message']['text'],obj.body);self.assertIn('/api/messages/session/',post.call_args.args[0])
        import requests
        post.side_effect=requests.ReadTimeout()
        with self.assertRaises(SendError) as caught:send(obj)
        self.assertTrue(caught.exception.uncertain)

    @patch.dict(os.environ,{'EMAIL_HOST':'smtp.example.test','DEFAULT_FROM_EMAIL':'office@example.test'})
    @override_settings(DEFAULT_FROM_EMAIL='office@example.test')
    @patch('communications.providers.EmailMessage.send',return_value=1)
    def test_email_adapter(self,send_mock):
        obj=self.queued();self.assertEqual(send(obj),str(obj.pk));send_mock.assert_called_once()

    @patch('communications.dispatch.configured',return_value=True)
    def test_confirmed_delivery_is_not_overwritten_by_late_timeout(self,configured):
        from .dispatch import event
        obj=self.queued()
        def callback(message):
            current=OutboundMessage.objects.get(pk=message.pk);event(current,'READ')
            raise SendError('TIMEOUT',uncertain=True)
        with patch('communications.dispatch.send',side_effect=callback):deliver(obj.pk)
        obj.refresh_from_db();self.assertEqual(obj.status,'READ')

    @patch('communications.dispatch.configured',return_value=True)
    @patch('communications.dispatch.send',return_value='paid-message')
    def test_per_channel_cost_budget_reserves_attempts(self,sender,configured):
        obj=self.queued('SMS')
        with override_settings(SMS_MESSAGE_COST_MINOR=10,COMMUNICATION_SMS_MONTHLY_BUDGET_MINOR=0):self.assertEqual(deliver(obj.pk),'MONTHLY_BUDGET');sender.assert_not_called()
        with override_settings(SMS_MESSAGE_COST_MINOR=10,COMMUNICATION_SMS_MONTHLY_BUDGET_MINOR=10):
            self.assertEqual(deliver(obj.pk),'SENT')
            second=self.queued('SMS');self.assertEqual(deliver(second.pk),'MONTHLY_BUDGET')
        self.assertEqual(MessageEvent.objects.get(message=obj,status='SENDING').estimated_cost_minor,10)

    def test_conversation_history_has_older_pages(self):
        obj=self.queued()
        OutboundMessage.objects.bulk_create([OutboundMessage(person=self.person,contact=self.contact,template=obj.template,consent=obj.consent,channel='EMAIL',recipient=self.contact.normalized_value,body='History fixture',scheduled_at=timezone.now(),created_by=self.admin) for _ in range(100)])
        first=self.client.get(self.path);self.assertTrue(first.data['has_more']);self.assertEqual(len(first.data['messages']),100)
        older=self.client.get(self.path,{'offset':100});self.assertFalse(older.data['has_more']);self.assertEqual(len(older.data['messages']),1)

    def test_student_email_unsubscribe_requires_confirmation(self):
        from django.core import signing
        obj=self.queued();token=signing.dumps({'person':str(self.person.pk),'channel':'EMAIL'},salt='communication-opt-out');path='/api/v1/communications/opt-out/'+token+'/'
        history=self.client.get(self.path);self.assertNotIn('/opt-out/',history.data['messages'][0]['body'])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(path).status_code,200);self.assertTrue(eligible(obj))
        self.assertEqual(self.client.post(path).status_code,200);obj.refresh_from_db();self.assertEqual(obj.status,'CANCELLED');self.assertFalse(eligible(obj))
        self.assertEqual(self.client.post('/api/v1/communications/opt-out/invalid/').status_code,403)

    @override_settings(SMS_PROVIDER_URL='https://sms.example.test',SMS_PROVIDER_KEY='key')
    @patch('communications.providers.requests.post')
    def test_sms_contract(self,post):
        obj=self.queued('SMS');post.return_value=Mock(ok=True,status_code=200,json=lambda:{'id':'sms-id'})
        self.assertEqual(send(obj),'sms-id');self.assertEqual(post.call_args.kwargs['headers']['Idempotency-Key'],str(obj.pk));self.assertEqual(post.call_args.kwargs['json']['client_reference'],str(obj.pk))

    @patch('communications.tasks.process_webhook.delay')
    @patch('communications.tasks.retrieve_lead.delay')
    @patch('communications.tasks.deliver_message.delay')
    @patch('communications.dispatch.configured',return_value=True)
    def test_celery_tick_publishes_durable_work_and_heartbeat(self,configured,deliver_mock,lead_mock,webhook_mock):
        from .tasks import communication_tick
        from .models import WorkerHeartbeat
        obj=self.queued();receipt=WebhookReceipt.objects.create(provider='email',event_key='task-test',payload={})
        communication_tick.run();deliver_mock.assert_called_once_with(str(obj.pk));webhook_mock.assert_called_once_with(receipt.pk);self.assertTrue(WorkerHeartbeat.objects.exists())


from unittest import skipUnless
from django.db import connection,connections
from django.test import TransactionTestCase


@skipUnless(connection.vendor=='postgresql','Concurrent dispatch requires PostgreSQL row locks')
class DispatchConcurrencyTests(TransactionTestCase):
    def test_two_workers_do_not_send_one_message_twice(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        branch=Branch.objects.create(name='Communication race',code='MRACE');actor=User.objects.create_user(username='dispatch-race')
        person=Person.objects.create(ref='BE-MRACE',full_name='Race Student',branch=branch,source=Source.objects.create(name='Communication race'))
        contact=ContactMethod.objects.create(person=person,type='EMAIL',raw_value='race@example.test',normalized_value='race@example.test',verified_at=timezone.now())
        template=MessageTemplate.objects.create(name='Race',channel='EMAIL',body='Hello',created_by=actor)
        ChannelConsent.objects.create(person=person,channel='EMAIL',allowed=True,evidence='Test',recorded_by=actor)
        obj=queue(person,contact,template,actor);barrier=Barrier(2)
        def run(_):
            try:barrier.wait(timeout=10);return deliver(obj.pk)
            finally:connections.close_all()
        with patch('communications.dispatch.configured',return_value=True),patch('communications.dispatch.send',return_value='race-id') as sender:
            with ThreadPoolExecutor(max_workers=2) as workers:list(workers.map(run,range(2)))
            self.assertEqual(sender.call_count,1)
        obj.refresh_from_db();self.assertEqual(obj.status,'SENT')

    def test_celery_worker_consumes_message_task(self):
        import time
        from celery.contrib.testing.worker import start_worker
        from config.celery import app
        from .tasks import deliver_message
        branch=Branch.objects.create(name='Worker test',code='MWORK');actor=User.objects.create_user(username='worker-test')
        person=Person.objects.create(ref='BE-MWORK',full_name='Worker Student',branch=branch,source=Source.objects.create(name='Worker test'))
        contact=ContactMethod.objects.create(person=person,type='EMAIL',raw_value='worker@example.test',normalized_value='worker@example.test',verified_at=timezone.now())
        template=MessageTemplate.objects.create(name='Worker',channel='EMAIL',body='Hello',created_by=actor)
        ChannelConsent.objects.create(person=person,channel='EMAIL',allowed=True,evidence='Test',recorded_by=actor)
        obj=queue(person,contact,template,actor)
        with patch('communications.dispatch.configured',return_value=True),patch('communications.dispatch.send',return_value='worker-id') as sender:
            with start_worker(app,pool='solo',perform_ping_check=False,queues=['communication-validation'],shutdown_timeout=10):
                deliver_message.apply_async(args=[str(obj.pk)],queue='communication-validation')
                for _ in range(50):
                    obj.refresh_from_db()
                    if obj.status=='SENT':break
                    time.sleep(.1)
                self.assertEqual(obj.status,'SENT');self.assertEqual(sender.call_count,1)
