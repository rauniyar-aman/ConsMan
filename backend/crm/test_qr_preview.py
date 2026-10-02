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
from qr.renderer import render,safe_design
from rest_framework.exceptions import ValidationError
import resvg_py


class QRPreviewTests(TestCase):
    def test_dedicated_wifi_without_campaign_and_password_preservation(self):
        with TemporaryDirectory() as media,override_settings(MEDIA_ROOT=media):
            created=self.client.post('/api/v1/qr/',{'label':'Guest Wi-Fi','branch_id':self.branch.pk,'content_type':'WIFI','content':{'ssid':'Office guest','password':'test-only-wifi-password','security':'WPA'},'design':{'logo':False}},format='json')
            self.assertEqual(created.status_code,201,created.data)
            self.assertNotIn('password',created.data['content'])
            path='/api/v1/qr/'+created.data['id']+'/'
            preview=self.client.post('/api/v1/qr/preview/',{'id':created.data['id'],'content':{'ssid':'Updated guest'},'design':{'logo':False}},format='json')
            self.assertEqual(preview.status_code,200,preview.data)
            changed=self.client.patch(path,{'content':{'ssid':'Updated guest','security':'WPA'}},format='json')
            self.assertEqual(changed.status_code,200,changed.data)
            qr=QRCode.objects.get(pk=created.data['id']);self.assertEqual(qr.content['password'],'test-only-wifi-password')
            pdf=self.client.get(path+'download/?format=pdf')
            self.assertEqual(pdf.status_code,200,getattr(pdf,'data',None))
            import pymupdf
            with pymupdf.open(stream=pdf.content,filetype='pdf') as document:
                self.assertNotIn('test-only-wifi-password',document[0].get_text())
                self.assertIn('Updated guest',document[0].get_text())

    def test_dedicated_whatsapp_without_campaign_opens_contact(self):
        with TemporaryDirectory() as media,override_settings(MEDIA_ROOT=media):
            created=self.client.post('/api/v1/qr/',{'label':'Counsellor WhatsApp','branch_id':self.branch.pk,'content_type':'WHATSAPP','content':{'phone':'+9779812345678','message':'Hello, study advice please.'},'design':{'logo':False}},format='json')
            self.assertEqual(created.status_code,201,created.data)
            asset=QRCode.objects.get(pk=created.data['id']).assets.get()
            self.assertTrue(asset.payload.startswith('https://wa.me/9779812345678?text='))
            self.assertIn('Hello',asset.payload)
            self.assertEqual(self.client.get('/api/v1/qr/'+created.data['id']+'/download/?format=png').status_code,200)

    def test_wifi_payload_escaping_and_validation(self):
        from qr.renderer import payload
        qr=QRCode(content_type='WIFI',content={'ssid':'Guest;Room','password':'test:pass,word','security':'WPA','hidden':True},design={'logo':False})
        expected=r'WIFI:T:WPA;S:Guest\;Room;P:test\:pass\,word;H:true;;'
        self.assertEqual(payload(qr),expected)
        png,svg,text,design=render(qr)
        self.assertEqual(zxingcpp.read_barcode(Image.open(io.BytesIO(png))).text,expected)
        qr.content={'ssid':'Guest','security':'nopass','password':'ignored'}
        self.assertEqual(payload(qr),'WIFI:T:nopass;S:Guest;H:false;;')
        for content in [{'ssid':''},{'ssid':'Guest','security':'unsupported'},{'ssid':'Guest','security':'WPA','password':''}]:
            qr.content=content
            with self.assertRaises(ValidationError):payload(qr)

    def test_logo_shapes_decode_in_png_and_svg(self):
        for shape in ['square','rounded','circle']:
            with self.subTest(shape=shape):
                png,svg,text,design=render(QRCode(code='shapecheck',design={'logo_shape':shape}))
                self.assertEqual(design['logo_shape'],shape)
                self.assertEqual(zxingcpp.read_barcode(Image.open(io.BytesIO(png))).text,text)
                raster=resvg_py.svg_to_bytes(svg_string=svg.decode(),width=1024,skip_system_fonts=True)
                self.assertEqual(zxingcpp.read_barcode(Image.open(io.BytesIO(raster))).text,text)
        with self.assertRaises(ValidationError):safe_design({'logo_shape':'unsupported'})

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
            first=qr.assets.get(version=1)
            from pathlib import Path
            Path(first.png.path).unlink();Path(first.svg.path).unlink()
            for fmt in ['png','svg','pdf']:
                recovered=self.client.get('/api/v1/qr/'+str(qr.pk)+'/download/?format='+fmt+'&version=1')
                self.assertEqual(recovered.status_code,200,getattr(recovered,'data',None))
                if fmt=='png':self.assertEqual(zxingcpp.read_barcode(Image.open(io.BytesIO(recovered.content))).text,'https://example.com/original')
            qr.refresh_from_db();self.assertEqual(qr.asset_version,2)
            invalid=self.client.patch('/api/v1/qr/'+created.data['id']+'/',{'content':{'url':'http://invalid.example'}},format='json')
            self.assertEqual(invalid.status_code,400)
            qr.refresh_from_db();self.assertEqual(qr.asset_version,2);self.assertEqual(qr.content['url'],'https://example.com/updated')
