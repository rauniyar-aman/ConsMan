import base64
import io
import copy
from botocore.exceptions import ClientError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from PIL import Image
import zxingcpp
import pymupdf as fitz
import resvg_py
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4,A5
from reportlab.lib.utils import ImageReader
from crm.models import Branch,Campaign
from crm.permissions import branch_scope
from crm.services import audit
from crm.operations import required_reason
from .models import QRCode,QRAsset
from .renderer import render,payload,LOGO,DEFAULT,MODULES,EYES,BALLS

def active(qr):
    return qr.status=='ACTIVE' and not qr.archived_at and (not qr.expires_at or qr.expires_at>timezone.now())

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
def qr_preview(request):
    from crm.permissions import scope
    scope(request.user,'qr')
    design=request.data.get('design',{})
    if request.data.get('id'):
        qr=get_object_or_404(branch_scope(request.user,QRCode.objects.all(),'qr'),pk=request.data['id'],archived_at__isnull=True)
        if not isinstance(design,dict):raise ValidationError('Design must be an object.')
        qr.design={**qr.design,**design}
        if 'content_type' in request.data:qr.content_type=request.data['content_type']
        if 'content' in request.data:
            if not isinstance(request.data['content'],dict):raise ValidationError('Content must be an object.')
            qr.content=request.data['content']
    else:
        content=request.data.get('content',{})
        if not isinstance(content,dict):raise ValidationError('Content must be an object.')
        qr=QRCode(code='draftpreview',content_type=request.data.get('content_type','REGISTRATION'),content=content,design=design)
    png,svg,text,design=render(qr)
    response=Response({'image':'data:image/png;base64,'+base64.b64encode(png).decode(),'draft':True})
    response['Cache-Control']='no-store'
    return response

def qr_data(qr):
    total=qr.submissions.count();verified=qr.submissions.filter(verified_at__isnull=False).count()
    return {'id':str(qr.pk),'code':qr.code,'label':qr.label,'branch_id':qr.branch_id,'branch_name':qr.branch.name,'campaign_id':qr.campaign_id,'campaign_name':qr.campaign.name,'content_type':qr.content_type,'content':qr.content,'status':qr.status,'expires_at':qr.expires_at,'design':{key:val for key,val in qr.design.items() if key!='logo_data'},'asset_version':qr.asset_version,'scan_count':qr.scan_count,'submissions':total,'verified':verified,'persons_created':qr.submissions.filter(outcome='CREATED').count(),'verification_rate':round(verified/max(total,1)*100,1),'url':payload(qr)}

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
def qr_list(request):
    if request.method=='GET':
        items=branch_scope(request.user,QRCode.objects.filter(archived_at__isnull=True),'qr').select_related('branch','campaign')
        return Response({'results':[qr_data(q) for q in items.order_by('-created_at')[:200]],'styles':{'modules':MODULES,'eye_frames':EYES,'eye_balls':BALLS,'default':DEFAULT}})
    branch=get_object_or_404(Branch,pk=request.data.get('branch_id'))
    from crm.permissions import scope,profile
    if scope(request.user,'qr')!='full' and branch.pk!=profile(request.user).branch_id:raise ValidationError('Use your own branch.')
    campaign=get_object_or_404(Campaign,pk=request.data.get('campaign_id'),active=True)
    label=serializers.CharField(max_length=160).run_validation(request.data.get('label'))
    kind=request.data.get('content_type','REGISTRATION')
    if kind not in ['REGISTRATION','WHATSAPP','URL','VCARD']:raise ValidationError('Unsupported QR type.')
    content=request.data.get('content',{})
    if not isinstance(content,dict):raise ValidationError('Content must be an object.')
    qr=QRCode.objects.create(label=label,branch=branch,campaign=campaign,content_type=kind,content=content,design=request.data.get('design',{}),created_by=request.user)
    create_asset(qr)
    audit(request,'QR_CREATED',qr,new={'code':qr.code,'type':kind})
    return Response(qr_data(qr),status=201)

def create_asset(qr):
    png,svg,text,design=render(qr)
    qr.asset_version+=1;qr.design=design;qr.save()
    asset=QRAsset(qr=qr,version=qr.asset_version,design=design,payload=text,decode_passed=True)
    asset.png.save(f'{qr.pk}-{qr.asset_version}.png',ContentFile(png),save=False)
    asset.svg.save(f'{qr.pk}-{qr.asset_version}.svg',ContentFile(svg),save=False)
    asset.save()
    return asset

def asset_bytes(qr,asset,kind):
    field=asset.png if kind=='png' else asset.svg
    try:
        with field.open('rb') as stream:return stream.read()
    except FileNotFoundError:
        pass
    except ClientError as exc:
        if exc.response.get('Error',{}).get('Code') not in ['404','NoSuchKey','NotFound']:raise
    snapshot=copy.copy(qr);snapshot.design=asset.design
    png,svg,text,design=render(snapshot,payload_text=asset.payload)
    return png if kind=='png' else svg

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['PATCH','POST'])
@transaction.atomic
def qr_update(request,pk):
    qr=get_object_or_404(branch_scope(request.user,QRCode.objects.select_for_update(),'qr'),pk=pk,archived_at__isnull=True)
    if request.method=='POST' and request.data.get('clone'):
        clone=QRCode.objects.create(label=f'{qr.label} (copy)'[:160],branch=qr.branch,campaign=qr.campaign,content_type=qr.content_type,content=qr.content,design=qr.design,created_by=request.user)
        create_asset(clone);audit(request,'QR_CLONED',clone,new={'original_id':str(qr.pk)})
        return Response(qr_data(clone),status=201)
    allowed={'design','label','status','expires_at','archive','reason','content_type','content','branch_id','campaign_id'}
    if set(request.data)-allowed:raise ValidationError('Unsupported QR update fields. The permanent code cannot be changed.')
    old={'label':qr.label,'branch_id':qr.branch_id,'campaign_id':qr.campaign_id,'content_type':qr.content_type,'content':qr.content,'asset_version':qr.asset_version}
    if 'branch_id' in request.data:
        branch=get_object_or_404(branch_scope(request.user,Branch.objects.all(),'qr',field='pk'),pk=request.data['branch_id'])
        qr.branch=branch
    if 'campaign_id' in request.data:qr.campaign=get_object_or_404(Campaign,pk=request.data['campaign_id'],active=True)
    if 'content_type' in request.data:
        if request.data['content_type'] not in ['REGISTRATION','WHATSAPP','URL','VCARD']:raise ValidationError('Unsupported QR type.')
        qr.content_type=request.data['content_type']
    if 'content' in request.data:
        if not isinstance(request.data['content'],dict):raise ValidationError('Content must be an object.')
        qr.content=request.data['content']
    if 'label' in request.data:qr.label=serializers.CharField(max_length=160).run_validation(request.data['label'])
    if 'status' in request.data:
        if request.data['status'] not in ['ACTIVE','PAUSED','EXPIRED']:raise ValidationError('Invalid QR status.')
        qr.status=request.data['status']
    if 'expires_at' in request.data:
        from crm.operations import safe_date
        qr.expires_at=safe_date(request.data['expires_at']) if request.data['expires_at'] else None
    if request.data.get('archive'):required_reason(request.data);qr.archived_at=timezone.now()
    if 'design' in request.data:
        if not isinstance(request.data['design'],dict):raise ValidationError('Design must be an object.')
        qr.design={**qr.design,**request.data['design']}
    if {'design','content','content_type'} & set(request.data):create_asset(qr)
    qr.save();audit(request,'QR_UPDATED',qr,old=old,new={'label':qr.label,'branch_id':qr.branch_id,'campaign_id':qr.campaign_id,'content_type':qr.content_type,'content':qr.content,'status':qr.status,'asset_version':qr.asset_version,'archived':bool(qr.archived_at)})
    return Response(qr_data(qr))

@extend_schema(responses=OpenApiTypes.BINARY)
@api_view(['GET'])
def qr_download(request,pk):
    qr=get_object_or_404(branch_scope(request.user,QRCode.objects.all(),'qr'),pk=pk)
    try:version=int(request.query_params.get('version',qr.asset_version))
    except ValueError:raise ValidationError('Invalid asset version.')
    asset=get_object_or_404(QRAsset,qr=qr,version=version,decode_passed=True)
    png=asset_bytes(qr,asset,'png')
    decoded=zxingcpp.read_barcode(Image.open(io.BytesIO(png)))
    if not decoded or decoded.text!=asset.payload:raise ValidationError('Stored asset failed decode validation. Restyle before downloading.')
    kind=request.query_params.get('format','png')
    if kind=='png':
        try:size=int(request.query_params.get('size',1024))
        except ValueError:raise ValidationError('Invalid image size.')
        if size not in [512,1024,2048,4096]:raise ValidationError('Use a 512, 1024, 2048, or 4096 pixel output.')
        image=Image.open(io.BytesIO(png));image=image.resize((size,round(size*image.height/image.width)),Image.Resampling.NEAREST)
        result=zxingcpp.read_barcode(image)
        if not result or result.text!=asset.payload:raise ValidationError('This output size failed decoding.')
        output=io.BytesIO();image.save(output,format='PNG');data=output.getvalue();mime='image/png'
    elif kind=='svg':
        data=asset_bytes(qr,asset,'svg')
        final=resvg_py.svg_to_bytes(svg_string=data.decode(),width=1024,skip_system_fonts=True)
        result=zxingcpp.read_barcode(Image.open(io.BytesIO(final)))
        if not result or result.text!=asset.payload:raise ValidationError('The SVG download failed decoding.')
        mime='image/svg+xml'
    elif kind=='pdf':
        layout=request.query_params.get('layout','a4')
        if layout not in ['a4','a5','tent']:raise ValidationError('Choose a4, a5, or tent.')
        size=A5 if layout=='a5' else A4
        output=io.BytesIO();pdf=canvas.Canvas(output,pagesize=size);width,height=size
        pdf.drawImage(str(LOGO),width/2-90,height-130,width=180,height=98,mask='auto',preserveAspectRatio=True)
        pdf.setFillColorRGB(.118,.227,.541);pdf.setFont('Helvetica-Bold',20);pdf.drawCentredString(width/2,height-165,'The Blessing Edu')
        pdf.setFont('Helvetica',13);pdf.drawCentredString(width/2,height-190,qr.branch.name)
        image=Image.open(io.BytesIO(png));qsize=min(width-100,300,(height-340)*image.width/image.height);qrheight=qsize*image.height/image.width;y=height-220-qrheight
        pdf.drawImage(ImageReader(io.BytesIO(png)),(width-qsize)/2,y,width=qsize,height=qrheight)
        pdf.setFont('Helvetica-Bold',16);pdf.drawCentredString(width/2,y-30,str(asset.design.get('caption','Scan to register'))[:80])
        pdf.setFont('Helvetica',8);pdf.drawCentredString(width/2,y-50,asset.payload[:110])
        if layout=='tent':
            # Discard the single poster page and draw two complete faces.
            output=io.BytesIO();pdf=canvas.Canvas(output,pagesize=A4);width,height=A4
            for face in range(2):
                pdf.saveState();pdf.translate(0,face*height/2);face_height=height/2
                if face:pdf.translate(width,face_height);pdf.rotate(180)
                pdf.drawImage(str(LOGO),width/2-60,face_height-80,width=120,height=65,mask='auto',preserveAspectRatio=True)
                pdf.setFillColorRGB(.118,.227,.541);pdf.setFont('Helvetica-Bold',15);pdf.drawCentredString(width/2,face_height-95,'The Blessing Edu')
                pdf.setFont('Helvetica',11);pdf.drawCentredString(width/2,face_height-113,qr.branch.name)
                qh=205;qw=qh*image.width/image.height;qy=face_height-130-qh
                pdf.drawImage(ImageReader(io.BytesIO(png)),(width-qw)/2,qy,width=qw,height=qh)
                pdf.setFont('Helvetica-Bold',13);pdf.drawCentredString(width/2,qy-20,str(asset.design.get('caption','Scan to register'))[:70])
                pdf.restoreState()
            pdf.setDash(3,3);pdf.line(15,height/2,width-15,height/2)
        pdf.save();data=output.getvalue();mime='application/pdf'
        with fitz.open(stream=data,filetype='pdf') as document:
            page=document[0];pixels=page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
            decoded=zxingcpp.read_barcodes(Image.open(io.BytesIO(pixels.tobytes('png'))))
            if not any(result.text==asset.payload for result in decoded):raise ValidationError('The PDF poster failed its final decode test.')
    else:raise ValidationError('Choose PNG, SVG, or PDF.')
    audit(request,'QR_DOWNLOADED',qr,new={'version':version,'format':kind})
    response=HttpResponse(data,content_type=mime);response['Content-Disposition']=f'attachment; filename="{qr.code}-v{version}.{kind}"';return response
