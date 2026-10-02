import json
from pathlib import Path
from types import SimpleNamespace
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from .form_config import form_config,validate_answers,FIELDS
from .api import VisitorSerializer
class VisitorFormTests(SimpleTestCase):
    def test_catalog_matches_frontend(self):
        self.assertEqual(FIELDS,json.loads((Path(__file__).resolve().parents[2]/'frontend/src/lib/visitor-fields.json').read_text(encoding='utf-8')))
    def test_defaults_and_locked_fields(self):
        conf=form_config({'form':{'fields':{'phone':{'required':False,'visible':False},'email':{'required':True},'guardian_name':{'visible':False}}}})
        fields={x['name']:x for x in conf['fields']}
        self.assertTrue(fields['phone']['required']);self.assertTrue(fields['phone']['visible'])
        self.assertTrue(fields['email']['required']);self.assertFalse(fields['guardian_name']['visible'])
    def test_invalid_configuration(self):
        for custom in [{'fields':{'unknown':{}}},{'fields':{'email':{'required':'yes'}}},{'title':''}]:
            with self.assertRaises(ValidationError):form_config({'form':custom})
    def test_required_answers(self):
        qr=SimpleNamespace(content={})
        with self.assertRaises(ValidationError) as error:validate_answers(qr,{'full_name':'Visitor','phone':'+9779801234599'})
        self.assertIn('address',error.exception.detail)
        validate_answers(qr,{'full_name':'Visitor','phone':'+9779801234599','address':'Kathmandu','highest_education':'Bachelor'})
    def test_reference_answers_validate(self):
        data={'code':'abc','full_name':'Visitor','phone':'+9779801234599','address':'Kathmandu','highest_education':'Bachelor','consent':True,'turnstile_token':'development','alternate_phone':'+9779801234598','preferred_university':'College','heard_about_us':'Walk-in','best_contact_method':'WHATSAPP','education':[{'level':'Bachelor','institute':'College','degree_stream':'Science','grade_or_percent':'3.5','passed_year':2025}],'test_scores':[{'test':'IELTS','score':'7','status':'TAKEN'}]}
        serializer=VisitorSerializer(data=data,context={'staff':True});self.assertTrue(serializer.is_valid(),serializer.errors)
        validate_answers(SimpleNamespace(content={'form':{'fields':{'institute':{'required':True},'test_IELTS':{'required':True}}}}),serializer.validated_data)

from django.test import TestCase,override_settings
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from crm.models import Branch,StaffProfile,Source,Campaign
from qr.models import QRCode
from unittest.mock import patch
class FormPublishingTests(TestCase):
    def setUp(self):
        branch=Branch.objects.create(name='Office',code='OFF')
        source=Source.objects.create(name='Walk-in');campaign=Campaign.objects.create(name='Visitors',source=source)
        self.user=User.objects.create_user('form-admin',password='test-password')
        self.qr=QRCode.objects.create(code='form-test',label='Visitors',branch=branch,campaign=campaign,content_type='REGISTRATION',content={},created_by=self.user)
        StaffProfile.objects.create(user=self.user,role='ADMIN',branch=branch)
        self.client=APIClient();self.client.force_authenticate(self.user)
    @patch('qr.api.create_asset')
    def test_publish_preview_required_fields_and_preserve_on_style_edit(self,asset):
        path=f'/api/v1/qr/{self.qr.pk}/'
        response=self.client.patch(path,{'content':{'form':{'title':'Office visitors','fields':{'email':{'required':True}}}}},format='json')
        self.assertEqual(response.status_code,200,response.data)
        public=APIClient().get('/api/public/qr/form-test/')
        self.assertEqual(public.status_code,200,public.data);self.assertEqual(public.data['form']['title'],'Office visitors')
        response=APIClient().post('/api/public/intake/submissions/',{'code':'form-test','full_name':'Visitor','phone':'+9779801234599','address':'Office','highest_education':'Bachelor','consent':True,'turnstile_token':'development'},format='json',HTTP_IDEMPOTENCY_KEY='form-test')
        self.assertEqual(response.status_code,400);self.assertIn('email',str(response.data))
        self.assertEqual(self.client.patch(path,{'content':{'url':''},'design':{'logo':False}},format='json').status_code,200)
        self.qr.refresh_from_db();self.assertEqual(self.qr.content['form']['title'],'Office visitors')
        self.client.force_authenticate(None)
        self.assertIn(self.client.patch(path,{'content':{}},format='json').status_code,[401,403])
