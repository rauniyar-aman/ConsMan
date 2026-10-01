import base64,io
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient
from PIL import Image
import zxingcpp
from crm.models import Branch,StaffProfile,AuditEvent,Campaign,Source
from qr.models import QRCode,QRAsset
from tempfile import TemporaryDirectory
from django.test import override_settings


class QRPreviewTests(TestCase):
    def setUp(self):
        self.branch=Branch.objects.create(name='Preview branch',code='PREVIEW')
        self.user=User.objects.create(username='preview-admin')
        StaffProfile.objects.create(user=self.user,role='ADMIN',branch=self.branch)
        self.client=APIClient();self.client.force_authenticate(self.user)

    def test_draft_decodes_and_does_not_save(self):
        before=AuditEvent.objects.count()
        response=self.client.post('/api/v1/qr/preview/',{'design':{'module':'rounded','logo':True}},format='json')
        self.assertEqual(response.status_code,200,response.data)
        result=zxingcpp.read_barcode(Image.open(io.BytesIO(base64.b64decode(response.data['image'].split(',')[1]))))
        self.assertTrue(result.text.endswith('/r/draftpreview'))
        self.assertEqual(QRCode.objects.count(),0)
        self.assertEqual(QRAsset.objects.count(),0)
        self.assertEqual(AuditEvent.objects.count(),before)

    def test_existing_preview_keeps_saved_design(self):
        source=Source.objects.create(name='Preview source')
        campaign=Campaign.objects.create(name='Preview campaign',source=source)
        qr=QRCode.objects.create(branch=self.branch,campaign=campaign,created_by=self.user,label='Saved',design={'module':'square','logo':False})
        response=self.client.post('/api/v1/qr/preview/',{'id':str(qr.pk),'design':{'module':'rounded'}},format='json')
        self.assertEqual(response.status_code,200,response.data)
        qr.refresh_from_db();self.assertEqual(qr.design['module'],'square');self.assertEqual(qr.asset_version,0)
        self.assertEqual(QRAsset.objects.count(),0)

    def test_invalid_design_and_denied_role(self):
        self.assertEqual(self.client.post('/api/v1/qr/preview/',{'design':{'module':'invalid'}},format='json').status_code,400)
        profile=self.user.staff;profile.role='COUNSELOR';profile.save()
        self.user.refresh_from_db();self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post('/api/v1/qr/preview/',{},format='json').status_code,403)

    def test_edit_content_keeps_code_and_versions_previous_asset(self):
        source=Source.objects.create(name='Edit source')
        campaign=Campaign.objects.create(name='Edit campaign',source=source)
        with TemporaryDirectory() as media,override_settings(MEDIA_ROOT=media):
            created=self.client.post('/api/v1/qr/',{'label':'Original','branch_id':self.branch.pk,'campaign_id':campaign.pk,'content_type':'URL','content':{'url':'https://example.com/original'},'design':{'logo':False}},format='json')
            self.assertEqual(created.status_code,201,created.data)
            response=self.client.patch('/api/v1/qr/'+created.data['id']+'/',{'label':'Updated','content':{'url':'https://example.com/updated'},'design':{'module':'rounded'}},format='json')
            self.assertEqual(response.status_code,200,response.data)
            self.assertEqual(response.data['code'],created.data['code'])
            self.assertEqual(response.data['asset_version'],2)
            qr=QRCode.objects.get(pk=created.data['id'])
            self.assertEqual(qr.label,'Updated')
            self.assertEqual(list(qr.assets.order_by('version').values_list('payload',flat=True)),['https://example.com/original','https://example.com/updated'])
            invalid=self.client.patch('/api/v1/qr/'+created.data['id']+'/',{'content':{'url':'http://invalid.example'}},format='json')
            self.assertEqual(invalid.status_code,400)
            qr.refresh_from_db();self.assertEqual(qr.asset_version,2);self.assertEqual(qr.content['url'],'https://example.com/updated')
