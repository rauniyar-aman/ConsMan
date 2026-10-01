import hashlib
import hmac
import json
from datetime import timedelta
import requests
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view,permission_classes,authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError,PermissionDenied,Throttled
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from crm.models import Person,AuditEvent,IdempotencyRecord
from crm.permissions import branch_scope,scoped_people,profile
from crm.services import normalize_phone,activity,audit
from crm.operations import required_reason
from qr.models import QRCode
from qr.api import active
from .models import IntakeSubmission,OtpChallenge,MessageDelivery,IntakeReview
from .services import policy,resume_token,resume,consume,create_challenge,deliver,submission_result,process_verified
from .providers import digest
from .network import client_ip

class VisitorSerializer(serializers.Serializer):
    code=serializers.CharField(max_length=24)
    full_name=serializers.CharField(max_length=160)
    phone=serializers.CharField(max_length=40)
    email=serializers.EmailField(required=False,allow_blank=True)
    consent=serializers.BooleanField()
    consent_version=serializers.CharField(max_length=30,default='visitor-v1')
    consent_channels=serializers.ListField(child=serializers.ChoiceField(choices=['CALL','SMS','WHATSAPP','EMAIL']),default=lambda:['CALL'],max_length=4)
    channel=serializers.ChoiceField(choices=['SMS','WHATSAPP'],default='SMS')
    turnstile_token=serializers.CharField(max_length=2048)
    website=serializers.CharField(required=False,allow_blank=True,max_length=100)
    preferred_country=serializers.CharField(max_length=80,required=False,allow_blank=True)
    preferred_countries=serializers.ListField(child=serializers.CharField(max_length=80),required=False,max_length=20)
    study_levels=serializers.ListField(child=serializers.CharField(max_length=80),required=False,max_length=20)
    preferred_course=serializers.CharField(max_length=160,required=False,allow_blank=True)
    preferred_intake=serializers.CharField(max_length=80,required=False,allow_blank=True)
    address=serializers.CharField(max_length=255,required=False,allow_blank=True)
    guardian_name=serializers.CharField(max_length=160,required=False,allow_blank=True)
    guardian_contact=serializers.CharField(max_length=40,required=False,allow_blank=True)
    highest_education=serializers.CharField(max_length=100,required=False,allow_blank=True)
    work_experience=serializers.CharField(max_length=3000,required=False,allow_blank=True)
    previous_visa_refusal=serializers.ChoiceField(choices=[('YES','Yes'),('NO','No'),('UNKNOWN','Unknown')],required=False)
    education=serializers.ListField(child=serializers.DictField(),required=False,max_length=20)
    test_scores=serializers.ListField(child=serializers.DictField(),required=False,max_length=20)
    def validate(self,data):
        if not data['consent'] or data.get('website'):raise ValidationError('Consent is required and the request must be valid.')
        phone=normalize_phone(data['phone'])
        if not self.context.get('staff') and not any(phone.startswith(prefix) for prefix in settings.OTP_ALLOWED_COUNTRY_PREFIXES):raise ValidationError({'phone':'This country code requires staff-assisted registration.'})
        for item in data.get('education',[]):
            if set(item)-{'level','institute','degree_stream','grade_or_percent','passed_year'} or not item.get('level') or not item.get('institute'):raise ValidationError('Invalid education fields.')
            if any(len(str(v))>160 for v in item.values()):raise ValidationError('Education text is too long.')
            if item.get('passed_year') is not None:serializers.IntegerField(min_value=1950,max_value=2200).run_validation(item['passed_year'])
        for item in data.get('test_scores',[]):
            if set(item)-{'test','score','status','test_date'} or item.get('test') not in ['IELTS','PTE','TOEFL','DUOLINGO','SAT','GRE','GMAT','OTHER']:raise ValidationError('Invalid test fields.')
            if item.get('status','PLANNED') not in ['TAKEN','PLANNED','NOT_TAKEN']:raise ValidationError('Invalid test status.')
            if item.get('test_date'):serializers.DateField().run_validation(item['test_date'])
            if len(str(item.get('score','')))>40:raise ValidationError('Score text is too long.')
        return data

def public_check(request):
    if len(request.body)>32768:raise ValidationError('Registration payload must be no more than 32 KB.')
    origin=request.headers.get('Origin')
    if origin and origin not in settings.PUBLIC_ALLOWED_ORIGINS:raise PermissionDenied('This form origin is not allowed.')

def turnstile(token):
    if settings.DEBUG and settings.INTAKE_DEV_BYPASS and token=='development':return
    if not settings.TURNSTILE_SECRET_KEY:raise ValidationError('Visitor registration is temporarily unavailable. Please see the front desk.')
    try:
        result=requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify',data={'secret':settings.TURNSTILE_SECRET_KEY,'response':token},timeout=(2,3)).json()
    except (requests.RequestException,ValueError):raise ValidationError('Verification service unavailable. Please retry.')
    if not result.get('success') or (settings.TURNSTILE_HOSTNAME and result.get('hostname')!=settings.TURNSTILE_HOSTNAME):raise ValidationError('Complete the verification check before submitting.')

@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
@permission_classes([AllowAny])
@authentication_classes([])
def public_qr(request,code):
    qr=get_object_or_404(QRCode.objects.select_related('branch'),code=code)
    enabled=active(qr) and qr.content_type=='REGISTRATION'
    if enabled:QRCode.objects.filter(pk=qr.pk).update(scan_count=F('scan_count')+1)
    return Response({'active':enabled,'branch':qr.branch.name,'turnstile_site_key':settings.TURNSTILE_SITE_KEY,'development_bypass':settings.DEBUG and settings.INTAKE_DEV_BYPASS,'whatsapp_available':bool(settings.WHATSAPP_GATEWAY_URL),'message':'This registration link is not active. Please see our front desk.' if not enabled else ''})

@extend_schema(request=VisitorSerializer,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def submit(request):
    public_check(request)
    allowed=set(VisitorSerializer().fields)
    if set(request.data)-allowed:raise ValidationError('Unsupported registration fields.')
    serializer=VisitorSerializer(data=request.data);serializer.is_valid(raise_exception=True)
    values=dict(serializer.validated_data)
    qr=get_object_or_404(QRCode.objects.select_related('branch','campaign__source'),code=values['code'])
    if not active(qr) or qr.content_type!='REGISTRATION':raise ValidationError('This registration link is not active. Please see the front desk.')
    key=request.headers.get('Idempotency-Key','')
    if not key or len(key)>160:raise ValidationError('A valid Idempotency-Key is required.')
    request_hash=hashlib.sha256(json.dumps({k:v for k,v in values.items() if k!='turnstile_token'},sort_keys=True).encode()).hexdigest()
    existing=IntakeSubmission.objects.filter(idempotency_key=key).first()
    if existing:
        if not hmac.compare_digest(existing.request_hash,request_hash):raise ValidationError('Idempotency key was used for different information.')
        return Response(submission_result(existing))
    turnstile(values.pop('turnstile_token'))
    values.pop('website',None)
    ip_hash=digest(client_ip(request))
    with transaction.atomic():
        consume(f'submit-ip:{ip_hash}',settings.PUBLIC_SUBMISSIONS_PER_DAY)
        submission,created=IntakeSubmission.objects.get_or_create(idempotency_key=key,defaults={'qr':qr,'payload':values,'phone_e164':normalize_phone(values['phone']),'channel_choice':values['channel'],'ip_hash':ip_hash,'request_hash':request_hash,'resume_token_hash':'','resume_expires_at':timezone.now()+timedelta(hours=24),'purge_after':timezone.now()+timedelta(days=policy('unverified_retention_days'))})
        if not created:
            if submission.request_hash!=request_hash:raise ValidationError('Idempotency key conflict.')
            return Response(submission_result(submission))
        submission.resume_token_hash=digest(resume_token(submission));submission.save(update_fields=['resume_token_hash'])
    # This transaction is complete before reserving or delivering any OTP.
    sent=False
    try:
        with transaction.atomic():
            submission=IntakeSubmission.objects.select_for_update(of=('self',)).get(pk=submission.pk)
            code,delivery,provider=create_challenge(submission,submission.channel_choice)
        sent=deliver(code,delivery,provider)
    except Throttled as error:
        IntakeSubmission.objects.filter(pk=submission.pk).update(unverified_reason='SEND_FAILED')
        AuditEvent.objects.create(action='OTP_LIMIT_REACHED',branch=qr.branch,object_id=str(submission.pk),request_id=request.request_id)
        from crm.services import notify_managers
        notify_managers(qr.branch,'OTP_LIMIT','OTP delivery limit requires review',key=f'otp-limit:{qr.branch_id}:{timezone.localdate()}',message=str(error.detail))
    submission.refresh_from_db()
    return Response(submission_result(submission,sent),status=201)

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST','PATCH'])
@permission_classes([AllowAny])
@authentication_classes([])
def verification(request,pk,operation='resume'):
    public_check(request)
    if operation=='resume':
        submission=resume(request,pk)
        return Response(submission_result(submission))
    if operation=='verify':
        with transaction.atomic():
            submission=resume(request,pk,True)
            if submission.verified_at:return Response({'verified':True,'message':'Thank you. Our team will contact you about your next steps.'})
            challenge=submission.challenges.select_for_update(of=('self',)).order_by('-last_sent_at').first()
            if not challenge:raise ValidationError('No code is available. Your details are saved.')
            if challenge.status=='LOCKED':return Response({'code':'otp_locked','message':'Your details are saved. Ask the front desk to verify your number.','attempts_left':0},status=400)
            if challenge.status!='PENDING' or challenge.expires_at<timezone.now():
                challenge.status='EXPIRED';challenge.save(update_fields=['status']);submission.unverified_reason='EXPIRED';submission.save(update_fields=['unverified_reason'])
                return Response({'code':'otp_expired','message':'This code expired. Your details are saved.'},status=400)
            candidate=str(request.data.get('code',''))
            if len(candidate)!=6 or not candidate.isdigit() or not hmac.compare_digest(challenge.code_hash,digest(f'{challenge.salt}:{candidate}')):
                challenge.attempts+=1;left=max(policy('otp_max_attempts')-challenge.attempts,0)
                challenge.status='LOCKED' if not left else 'PENDING';challenge.save()
                submission.unverified_reason='LOCKED' if not left else 'WRONG_OTP';submission.save(update_fields=['unverified_reason'])
                if not left:AuditEvent.objects.create(action='OTP_LOCKED',branch=submission.qr.branch,object_id=str(submission.pk),request_id=request.request_id)
                return Response({'code':'otp_invalid','message':'Incorrect code. Your details are saved.','attempts_left':left},status=400)
            challenge.status='VERIFIED';challenge.save()
            submission.status='VERIFIED';submission.verified_at=timezone.now();submission.verified_via=f'OTP_{challenge.channel}';submission.unverified_reason='';submission.save()
            process_verified(request,submission)
        return Response({'verified':True,'message':'Thank you. Our team will contact you about your next steps.'})
    if operation not in ['resend','phone']:raise ValidationError('Unknown verification operation.')
    with transaction.atomic():
        submission=resume(request,pk,True)
        retry_key=request.headers.get('Idempotency-Key','')
        retry_hash=hashlib.sha256(json.dumps(request.data,sort_keys=True).encode()).hexdigest()
        reservation=None
        if retry_key:
            if len(retry_key)>160:raise ValidationError('Invalid Idempotency-Key.')
            reservation,created=IdempotencyRecord.objects.get_or_create(scope=f'intake:{pk}:{operation}',key=retry_key,defaults={'request_hash':retry_hash})
            if not created:
                if reservation.request_hash!=retry_hash:raise ValidationError('Idempotency key was used for different information.')
                return Response(submission_result(submission))
        channel=request.data.get('channel',submission.channel_choice)
        if channel not in ['SMS','WHATSAPP']:raise ValidationError('Invalid channel.')
        if operation=='phone':
            if submission.status!='UNVERIFIED' or submission.corrections>=2:raise ValidationError('Phone correction limit reached. Your details are saved.')
            number=normalize_phone(request.data.get('phone',''))
            if not any(number.startswith(prefix) for prefix in settings.OTP_ALLOWED_COUNTRY_PREFIXES):raise ValidationError('Country code is not allowed.')
            old=submission.phone_e164;submission.phone_e164=number;submission.payload={**submission.payload,'phone':request.data['phone']};submission.corrections+=1;submission.save()
            AuditEvent.objects.create(action='INTAKE_PHONE_CORRECTED',branch=submission.qr.branch,object_id=str(submission.pk),request_id=request.request_id,old={'phone':old},new={'phone':number})
        code,delivery,provider=create_challenge(submission,channel)
    sent=deliver(code,delivery,provider)
    submission.refresh_from_db();return Response(submission_result(submission,sent))

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
def staff_queue(request,pk=None,operation=None):
    items=branch_scope(request.user,IntakeSubmission.objects.select_related('qr__branch','qr__campaign__source','person'),'intake',field='qr__branch')
    if request.method=='GET':
        state=request.query_params.get('status')
        if state:items=items.filter(status=state)
        if request.query_params.get('outcome'):items=items.filter(outcome=request.query_params['outcome'])
        return Response([{'id':str(s.pk),'full_name':s.payload.get('full_name',''),'phone':s.phone_e164,'payload':s.payload,'branch':s.qr.branch.name,'qr_label':s.qr.label,'status':s.status,'outcome':s.outcome,'unverified_reason':s.unverified_reason,'verified_via':s.verified_via,'created_at':s.created_at,'person_id':str(s.person_id) if s.person_id else None,'review':list(IntakeReview.objects.filter(submission=s).values('candidate_ids','confidence','resolved_at'))} for s in items.order_by('-created_at')[:300]])
    submission=get_object_or_404(items.select_for_update(of=('self',)),pk=pk)
    reason=required_reason(request.data)
    if operation=='verify-by-call':
        if submission.status!='UNVERIFIED':raise ValidationError('Only unverified submissions can be verified by call.')
        submission.status='VERIFIED';submission.verified_at=timezone.now();submission.verified_by_staff=request.user;submission.verified_via='STAFF_CALL';submission.save()
        process_verified(request,submission)
    elif operation=='discard':
        if submission.status not in ['UNVERIFIED','VERIFIED']:raise ValidationError('This submission cannot be discarded.')
        submission.status='DISCARDED';submission.save()
        submission.challenges.filter(status='PENDING').update(status='SUPERSEDED')
    elif operation=='edit':
        if submission.status!='UNVERIFIED':raise ValidationError('Only unverified submissions may be edited here.')
        changes=request.data.get('payload',{})
        if not isinstance(changes,dict) or set(changes)-set(VisitorSerializer().fields):raise ValidationError('Unsupported payload fields.')
        forbidden={'code','turnstile_token','consent_version','consent'}
        if set(changes)&forbidden:raise ValidationError('Attribution and consent cannot be changed.')
        combined={**submission.payload,**changes,'turnstile_token':'staff','code':submission.qr.code}
        ser=VisitorSerializer(data=combined);ser.is_valid(raise_exception=True)
        payload=dict(ser.validated_data);payload.pop('turnstile_token',None)
        if 'phone' in changes:
            submission.phone_e164=normalize_phone(changes['phone']);submission.challenges.filter(status='PENDING').update(status='SUPERSEDED')
        submission.payload=payload;submission.save()
    elif operation=='resolve':
        if not submission.verified_at:raise ValidationError('Unverified submissions cannot create or match Persons.')
        person=None
        if request.data.get('person_id'):person=get_object_or_404(scoped_people(request.user,Person.objects.filter(archived_at__isnull=True),'intake'),pk=request.data['person_id'])
        if not person and request.data.get('create') is not True:raise ValidationError('Choose an existing person or explicitly create a separate one.')
        if person:
            submission.status='VERIFIED';process_verified(request,submission,chosen_person=person)
        else:
            submission.status='VERIFIED';process_verified(request,submission,allow_create=True)
        IntakeReview.objects.filter(submission=submission).update(resolved_at=timezone.now())
    else:raise ValidationError('Unknown staff operation.')
    AuditEvent.objects.create(actor=request.user,branch=submission.qr.branch,action=f'INTAKE_{operation.upper()}',object_id=str(submission.pk),request_id=request.request_id,new={'reason':reason,'status':submission.status})
    return Response({'ok':True,'person_id':str(submission.person_id) if submission.person_id else None})

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
def staff_assisted(request):
    from crm.permissions import scope
    scope(request.user,'create')
    if request.method=='GET':
        codes=QRCode.objects.filter(status='ACTIVE',content_type='REGISTRATION',archived_at__isnull=True)
        if profile(request.user).role!='ADMIN':codes=codes.filter(branch=profile(request.user).branch)
        return Response([{'id':q.code,'name':q.label} for q in codes if active(q)])
    qr=get_object_or_404(QRCode,code=request.data.get('code'),archived_at__isnull=True)
    if not active(qr) or qr.content_type!='REGISTRATION':raise ValidationError('Choose an active registration code.')
    if profile(request.user).role!='ADMIN' and qr.branch_id!=profile(request.user).branch_id:raise PermissionDenied()
    data={k:v for k,v in request.data.items() if k in VisitorSerializer().fields};data['turnstile_token']='staff'
    ser=VisitorSerializer(data=data,context={'staff':True});ser.is_valid(raise_exception=True)
    temperature=request.data.get('temperature','WARM')
    if temperature not in ['HOT','WARM','COLD']:raise ValidationError('Invalid temperature.')
    values=dict(ser.validated_data);values.pop('turnstile_token')
    import uuid
    sub=IntakeSubmission.objects.create(qr=qr,payload=values,phone_e164=normalize_phone(values['phone']),status='VERIFIED',verified_at=timezone.now(),verified_by_staff=request.user,verified_via='STAFF_IN_PERSON',resume_token_hash='',resume_expires_at=timezone.now(),ip_hash='',idempotency_key=str(uuid.uuid4()),request_hash='',purge_after=timezone.now()+timedelta(days=90))
    person=process_verified(request,sub)
    if person:
        if profile(request.user).role=='COUNSELOR' and person.owner_id!=request.user.pk and sub.outcome=='CREATED':
            from crm.services import assign_owner
            assign_owner(request,person,request.user,'Staff-assisted intake')
        if not scoped_people(request.user,Person.objects.filter(pk=person.pk),'work').exists():
            return Response({'id':str(sub.pk),'person_id':None,'message':'Saved for manager review.'},status=201)
        if sub.outcome=='CREATED':
            person.temperature=temperature;person.save()
            if temperature=='HOT':
                from crm.calendar import start_sla
                start_sla(person,'HOT_CONTACT')
            audit(request,'STAFF_INTAKE_TEMPERATURE',person,new={'temperature':temperature})
        if request.data.get('summary'):activity(request,person,'Staff-assisted counseling',str(request.data['summary'])[:3000],'MEETING')
        if request.data.get('next_step'):
            from crm.models import FollowUp
            from crm.operations import safe_date
            FollowUp.objects.create(person=person,owner=person.owner,subject=str(request.data['next_step'])[:200],due_at=safe_date(request.data.get('next_due_at')))
            from crm.services import refresh_next_action
            refresh_next_action(person)
    return Response({'id':str(sub.pk),'person_id':str(person.pk) if person else None},status=201)

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def gateway_webhook(request):
    if not settings.WHATSAPP_GATEWAY_WEBHOOK_SECRET:raise PermissionDenied('Webhook is not configured.')
    expected='sha256='+hmac.new(settings.WHATSAPP_GATEWAY_WEBHOOK_SECRET.encode(),request.body,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,request.headers.get('X-Webhook-Signature','')):raise PermissionDenied('Invalid signature.')
    if request.data.get('sessionId')!=settings.WHATSAPP_GATEWAY_SESSION:raise PermissionDenied('Invalid session.')
    data=request.data.get('data',{});event=request.data.get('event')
    if event=='message.sent':
        key=data.get('key',{});jid=data.get('to') or data.get('from') or key.get('remoteJid','')
        phone='+'+str(jid).split('@')[0]
        content=str(data.get('content',''))
        delivery=MessageDelivery.objects.filter(provider='wa-akg',to_hash=digest(phone),content_hash=digest(content),created_at__gte=timezone.now()-timedelta(hours=1)).order_by('-created_at').first()
        if delivery:MessageDelivery.objects.filter(pk=delivery.pk).update(provider_message_id=str(key.get('id',''))[:160])
    elif event=='message.status':
        state=data.get('status')
        if state in ['SENT','DELIVERED','READ','FAILED']:
            items=MessageDelivery.objects.filter(provider='wa-akg',provider_message_id=str(data.get('keyId',''))).exclude(provider_message_id='')
            if state in ['FAILED','SENT']:items=items.exclude(status='DELIVERED')
            items.update(status='DELIVERED' if state=='READ' else state)
    return Response({'ok':True})
