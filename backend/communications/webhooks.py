import hashlib,hmac,os,re
from datetime import timedelta
import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view,authentication_classes,permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import PermissionDenied,ValidationError
from rest_framework.response import Response
from django.http import HttpResponse
from django.core import signing
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from crm.models import ContactMethod,Person
from .models import WebhookReceipt,InboundMessage,OutboundMessage,LeadForm,SocialLead,MessageEvent
from .dispatch import event


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.STR)
@api_view(['GET','POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def opt_out(request,token):
    try:data=signing.loads(token,salt='communication-opt-out',max_age=365*86400)
    except signing.BadSignature:raise PermissionDenied('Invalid or expired unsubscribe link.')
    if data.get('channel')!='EMAIL':raise PermissionDenied('Invalid unsubscribe channel.')
    person=Person.objects.filter(pk=data.get('person')).first()
    if not person:raise PermissionDenied('Invalid unsubscribe link.')
    if request.method=='POST':
        from .models import ChannelConsent
        with transaction.atomic():
            Person.objects.select_for_update().get(pk=person.pk)
            current=ChannelConsent.objects.filter(person=person,channel='EMAIL').order_by('-pk').first()
            if not current or current.allowed:
                ChannelConsent.objects.create(person=person,channel='EMAIL',allowed=False,evidence='Student used signed unsubscribe link')
                for message in OutboundMessage.objects.select_for_update().filter(person=person,channel='EMAIL',status='QUEUED'):event(message,'CANCELLED','STUDENT_UNSUBSCRIBED')
        content='<h1>Unsubscribed</h1><p>You will no longer receive automated email messages from ConsMan.</p>'
    else:content='<h1>Unsubscribe from email</h1><p>Confirm to stop email messages from ConsMan.</p><form method="post"><button type="submit">Unsubscribe</button></form>'
    response=HttpResponse('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Email preferences | ConsMan</title></head><body style="font:18px sans-serif;padding:24px;max-width:600px;margin:auto">'+content+'</body></html>')
    response['Cache-Control']='no-store';response['Referrer-Policy']='no-referrer';return response


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def receive(request,provider):
    if provider not in ['gateway','email','sms','meta']:raise ValidationError('Unknown provider.')
    if request.method=='GET':
        token=os.getenv('META_VERIFY_TOKEN','')
        if provider!='meta' or not token or request.query_params.get('hub.mode')!='subscribe' or not hmac.compare_digest(token,request.query_params.get('hub.verify_token','')):raise PermissionDenied('Invalid verification.')
        return HttpResponse(request.query_params.get('hub.challenge','')[:500],content_type='text/plain')
    secret=settings.WHATSAPP_GATEWAY_WEBHOOK_SECRET if provider=='gateway' else os.getenv('META_APP_SECRET','') if provider=='meta' else os.getenv(provider.upper()+'_WEBHOOK_SECRET','')
    if not secret:raise PermissionDenied('Webhook is not configured.')
    raw=request.body
    if len(raw)>65536:raise ValidationError('Webhook payload too large.')
    expected='sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    header='X-Hub-Signature-256' if provider=='meta' else 'X-Webhook-Signature'
    if not hmac.compare_digest(expected,request.headers.get(header,'')):raise PermissionDenied('Invalid signature.')
    if not isinstance(request.data,dict):raise ValidationError('Expected an object.')
    if provider=='gateway' and request.data.get('sessionId')!=settings.WHATSAPP_GATEWAY_SESSION:raise PermissionDenied('Invalid session.')
    obj,created=WebhookReceipt.objects.get_or_create(provider=provider,event_key=hashlib.sha256(raw).hexdigest(),defaults={'payload':request.data})
    return Response({'accepted':True,'duplicate':not created},status=200 if provider=='meta' else 202)


def inbound(channel,sender,body,provider_id):
    if not sender or not provider_id:return
    sender=sender.strip().lower() if channel=='EMAIL' else sender.strip()
    # Shared numbers stay unmatched until a staff reviewer identifies the person.
    ids=list(ContactMethod.objects.filter(type='EMAIL' if channel=='EMAIL' else 'PHONE',normalized_value=sender,verified_at__isnull=False,person__archived_at__isnull=True,person__merged_into__isnull=True).values_list('person_id',flat=True).distinct()[:2])
    person_id=ids[0] if len(ids)==1 else None
    obj,created=InboundMessage.objects.get_or_create(channel=channel,provider_id=provider_id[:200],defaults={'sender':sender[:254],'body':body[:16000],'person_id':person_id})
    if created and body.strip().upper() in ['STOP','UNSUBSCRIBE','CANCEL']:
        from .models import ChannelConsent
        # Opt-out applies to all matching contacts, including shared numbers.
        for person in Person.objects.filter(pk__in=ContactMethod.objects.filter(normalized_value=sender,type='EMAIL' if channel=='EMAIL' else 'PHONE').values('person_id')):
            with transaction.atomic():
                Person.objects.select_for_update().get(pk=person.pk)
                ChannelConsent.objects.create(person=person,channel=channel,allowed=False,evidence='Inbound opt-out '+provider_id[:100])
                for message in OutboundMessage.objects.select_for_update().filter(person=person,channel=channel,status='QUEUED'):event(message,'CANCELLED','INBOUND_OPT_OUT')


def status_update(obj,state):
    ranks={'SENDING':0,'UNKNOWN':0,'FAILED':0,'SENT':1,'DELIVERED':2,'READ':3}
    if state not in ['SENT','DELIVERED','READ','FAILED'] or obj.status=='CANCELLED':return
    if state=='FAILED' and ranks.get(obj.status,0)>=2:return
    if state!='FAILED' and ranks.get(state,0)<ranks.get(obj.status,0):return
    if obj.status!=state:event(obj,state)


def process(pk):
    with transaction.atomic():
        receipt=WebhookReceipt.objects.select_for_update().get(pk=pk)
        if receipt.status=='PROCESSED':return
        payload=receipt.payload;provider=receipt.provider
        if provider=='gateway':
            data=payload.get('data',{});kind=payload.get('event');key=data.get('key',{})
            if not isinstance(data,dict) or not isinstance(key,dict):raise ValidationError('Invalid Gateway event.')
            jid=str(data.get('from') or key.get('remoteJid',''));sender='+'+jid.split('@')[0]
            if kind=='message.received' and not data.get('isGroup') and '@s.whatsapp.net' in jid:
                inbound('WHATSAPP',sender,str(data.get('content','')),str(key.get('id','')))
            elif kind=='message.sent':
                candidates=OutboundMessage.objects.select_for_update().filter(channel='WHATSAPP',recipient=sender,body=str(data.get('content','')),provider_id='',status__in=['SENDING','SENT','UNKNOWN'], ).filter(Q(claimed_at__gte=timezone.now()-timedelta(days=1))|Q(claimed_at__isnull=True,created_at__gte=timezone.now()-timedelta(days=1))).order_by('claimed_at')
                obj=candidates.first()
                if obj and key.get('id'):
                    obj.provider_id=str(key['id'])[:200];status_update(obj,'SENT');obj.save(update_fields=['provider_id'])
            elif kind=='message.status':
                ident=str(data.get('keyId',''))
                obj=OutboundMessage.objects.select_for_update().filter(channel='WHATSAPP',provider_id=ident).first() if ident else None
                if obj:status_update(obj,str(data.get('status','')))
                else:raise ValidationError('Awaiting provider message binding.')
        elif provider in ['email','sms']:
            channel=provider.upper()
            if payload.get('event')=='inbound':inbound(channel,str(payload.get('from','')),str(payload.get('body','')),str(payload.get('id','')))
            else:
                obj=OutboundMessage.objects.select_for_update().filter(channel=channel,provider_id=str(payload.get('id',''))).first() if payload.get('id') else None
                if obj:status_update(obj,str(payload.get('status','')))
                else:raise ValidationError('Awaiting provider message binding.')
        else:
            for entry in payload.get('entry',[])[:50]:
                for change in entry.get('changes',[])[:50]:
                    if change.get('field')!='leadgen':continue
                    value=change.get('value',{});form=LeadForm.objects.filter(form_id=str(value.get('form_id','')),page_id=str(value.get('page_id',entry.get('id',''))),enabled=True).first()
                    lead_id=str(value.get('leadgen_id',''))
                    if form and re.fullmatch(r'\d{1,100}',lead_id):SocialLead.objects.get_or_create(lead_id=lead_id,defaults={'form':form})
        receipt.status='PROCESSED';receipt.error_code='';receipt.save(update_fields=['status','error_code'])


def fetch_lead(pk):
    lead=SocialLead.objects.select_related('form').get(pk=pk)
    if lead.status!='PENDING' or not lead.form.enabled:return
    token=os.getenv('META_PAGE_ACCESS_TOKEN','');version=os.getenv('META_GRAPH_VERSION','')
    if not token or not re.fullmatch(r'v\d+\.\d+',version):return
    response=requests.get(f'https://graph.facebook.com/{version}/{lead.lead_id}',params={'fields':'id,form_id,field_data,created_time'},headers={'Authorization':'Bearer '+token},timeout=(3,10),allow_redirects=False)
    if not response.ok:raise ValidationError('META_FETCH_FAILED')
    payload=response.json()
    if str(payload.get('id'))!=lead.lead_id or str(payload.get('form_id'))!=lead.form.form_id:raise ValidationError('META_LEAD_ID_MISMATCH')
    with transaction.atomic():
        lead=SocialLead.objects.select_for_update().get(pk=pk)
        if lead.status!='PENDING':return
        lead.payload=payload;lead.status='REVIEW';lead.error_code='';lead.save(update_fields=['payload','status','error_code'])
