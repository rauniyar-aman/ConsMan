import hmac
import secrets
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import F,Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError, Throttled, PermissionDenied
from crm.models import Person, ContactMethod, ConsentRecord, SystemPolicy, AuditEvent
from crm.services import normalize_phone,next_reference, duplicate_signals, activity, audit, assign_owner, automatic_owner, notify, notify_managers, refresh_next_action
from crm.calendar import start_sla
from .models import IntakeSubmission,OtpChallenge,MessageDelivery,RateBucket,RateEvent,IntakeReview
from .providers import digest,get_provider,DeliveryFailure

DEFAULTS={'otp_valid_minutes':5,'otp_max_attempts':5,'otp_cooldown_seconds':60,'otp_max_sends':3,'otp_phone_daily_limit':5,'otp_ip_daily_limit':20,'otp_qr_daily_limit':1000,'otp_global_daily_limit':10000,'otp_monthly_budget_minor':100000,'unverified_retention_days':30,'resolved_intake_retention_days':90}

def policy(key):
    value=SystemPolicy.objects.filter(key=key).values_list('value',flat=True).first()
    return int(value) if value is not None else DEFAULTS[key]

def resume_token(submission):
    return digest(f'resume:{submission.pk}:{submission.request_hash}')

def resume(request,pk,lock=False):
    token=request.headers.get('X-Resume-Token','')
    query=IntakeSubmission.objects.select_for_update(of=('self',)) if lock else IntakeSubmission.objects
    submission=query.filter(pk=pk).first()
    if not submission or not token or not hmac.compare_digest(submission.resume_token_hash,digest(token)) or submission.resume_expires_at<timezone.now():raise PermissionDenied('Verification session expired. Ask the front desk for help.')
    return submission

def consume(key,limit,amount=1,period='day'):
    now=timezone.now();stamp=now.strftime('%Y-%m') if period=='month' else 'rolling-24h'
    bucket,_=RateBucket.objects.get_or_create(key=f'{key}:{stamp}',defaults={'expires_at':now+timedelta(days=32 if period=='month' else 2)})
    bucket=RateBucket.objects.select_for_update(of=('self',)).get(pk=bucket.pk)
    if period!='month':bucket.count=RateEvent.objects.filter(key=bucket.key,at__gt=now-timedelta(hours=24)).aggregate(total=Sum('amount'))['total'] or 0
    if bucket.count+amount>limit:raise Throttled(detail='Verification limit reached. Your details are saved; ask the front desk for help.')
    bucket.count+=amount;bucket.save(update_fields=['count'])
    if period!='month':RateEvent.objects.create(key=bucket.key,amount=amount)

def create_challenge(submission,channel):
    now=timezone.now()
    if submission.status!='UNVERIFIED':raise ValidationError('This submission cannot receive another code.')
    latest=submission.challenges.order_by('-last_sent_at').first()
    if latest and (now-latest.last_sent_at).total_seconds()<policy('otp_cooldown_seconds'):raise Throttled(wait=policy('otp_cooldown_seconds'),detail='Please wait before requesting another code.')
    if submission.send_count>=policy('otp_max_sends'):raise Throttled(detail='Send limit reached. Your details are saved.')
    consume(f'phone:{digest(submission.phone_e164)}',policy('otp_phone_daily_limit'))
    consume(f'ip:{submission.ip_hash}',policy('otp_ip_daily_limit'))
    consume(f'qr:{submission.qr_id}',policy('otp_qr_daily_limit'))
    consume('global',policy('otp_global_daily_limit'))
    cost=settings.SMS_MESSAGE_COST_MINOR if channel=='SMS' else settings.WHATSAPP_MESSAGE_COST_MINOR
    try:consume('spend',policy('otp_monthly_budget_minor'),cost,'month')
    except Throttled:raise Throttled(detail='Monthly OTP budget reached. Your details are saved; ask the front desk for help.')
    submission.challenges.filter(status='PENDING').update(status='SUPERSEDED')
    code=f'{secrets.randbelow(1000000):06d}';salt=secrets.token_hex(16)
    challenge=OtpChallenge.objects.create(submission=submission,phone_e164=submission.phone_e164,channel=channel,salt=salt,code_hash=digest(f'{salt}:{code}'),expires_at=now+timedelta(minutes=policy('otp_valid_minutes')),last_sent_at=now)
    submission.send_count+=1;submission.channel_choice=channel;submission.save(update_fields=['send_count','channel_choice','updated_at'])
    provider=get_provider(channel)
    delivery=MessageDelivery.objects.create(challenge=challenge,provider=provider.name,channel=channel,to_hash=digest(submission.phone_e164),cost_minor=cost)
    return code,delivery,provider

def deliver(code,delivery,provider):
    """Called only after submission and challenge transactions have committed."""
    try:
        identifier=provider.send(delivery.challenge.phone_e164,code,delivery)
        MessageDelivery.objects.filter(pk=delivery.pk,status='QUEUED').update(status='SENT')
        if identifier:MessageDelivery.objects.filter(pk=delivery.pk).update(provider_message_id=identifier)
        IntakeSubmission.objects.filter(pk=delivery.challenge.submission_id,status='UNVERIFIED').update(unverified_reason='NOT_ENTERED')
        return True
    except Exception as error:
        # Keep only fixed provider error codes, never response bodies or OTP contents.
        MessageDelivery.objects.filter(pk=delivery.pk).update(status='FAILED',error=str(error)[:60] if isinstance(error,DeliveryFailure) else 'PROVIDER_ERROR')
        IntakeSubmission.objects.filter(pk=delivery.challenge.submission_id,status='UNVERIFIED').update(unverified_reason='SEND_FAILED')
        return False

def submission_result(submission,sent=None):
    latest=submission.challenges.order_by('-last_sent_at').first()
    left=max(0,policy('otp_max_attempts')-(latest.attempts if latest else 0))
    cooldown=max(0,policy('otp_cooldown_seconds')-int((timezone.now()-latest.last_sent_at).total_seconds())) if latest else 0
    return {'id':str(submission.pk),'resume_token':resume_token(submission),'status':submission.status,'send_failed':submission.unverified_reason=='SEND_FAILED' if sent is None else not sent,'attempts_left':left,'resend_after':cooldown,'message':'Your details are saved. Verify your number to continue, or ask the front desk for help.'}

def process_verified(request,submission,chosen_person=None,allow_create=False):
    if not submission.verified_at or submission.status not in ['VERIFIED','PROCESSED']:raise ValidationError('Only verified submissions enter the CRM pipeline.')
    if submission.status=='PROCESSED' and submission.outcome!='NEEDS_REVIEW':return submission.person
    payload=submission.payload
    matches=duplicate_signals(submission.phone_e164,payload.get('email','').strip().lower(),payload['full_name'],payload.get('dob'),country=payload.get('preferred_country',''))
    strong=[m for m in matches if m['confidence'] in ['EXACT','HIGH']]
    medium=[m for m in matches if m['confidence']=='MEDIUM']
    if chosen_person:
        person=chosen_person;submission.outcome='MATCHED'
    elif strong and not allow_create:
        unique={m['person'].pk:m['person'] for m in strong}
        person=next(iter(unique.values())) if len(unique)==1 else None
        submission.outcome='MATCHED' if person else 'NEEDS_REVIEW'
        IntakeReview.objects.update_or_create(submission=submission,defaults={'candidate_ids':[str(m['person'].pk) for m in strong],'confidence':strong[0]['confidence']})
    elif medium and not allow_create:
        person=None;submission.outcome='NEEDS_REVIEW'
        IntakeReview.objects.update_or_create(submission=submission,defaults={'candidate_ids':[str(m['person'].pk) for m in medium],'confidence':'MEDIUM'})
    else:
        qr=submission.qr
        person=Person.objects.create(ref=next_reference(),full_name=payload['full_name'],branch=qr.branch,source=qr.campaign.source,campaign=qr.campaign,created_by=request.user if request.user.is_authenticated else None,preferred_country=payload.get('preferred_country',''),preferred_course=payload.get('preferred_course',''),preferred_intake=payload.get('preferred_intake',''),preferred_university=payload.get('preferred_university',''),best_contact_method=payload.get('best_contact_method','CALL'),address=payload.get('address',''),guardian_name=payload.get('guardian_name',''),guardian_contact=payload.get('guardian_contact',''),highest_education=payload.get('highest_education',''),work_experience=payload.get('work_experience',''),previous_visa_refusal=payload.get('previous_visa_refusal','UNKNOWN'),preferred_countries=payload.get('preferred_countries',[]),study_levels=payload.get('study_levels',[]))
        ContactMethod.objects.create(person=person,type='PHONE',raw_value=payload['phone'],normalized_value=submission.phone_e164,is_primary=True,verified_at=submission.verified_at,verified_via=submission.verified_via)
        if payload.get('email'):ContactMethod.objects.create(person=person,type='EMAIL',raw_value=payload['email'],normalized_value=payload['email'].strip().lower(),is_primary=True)
        if payload.get('alternate_phone'):ContactMethod.objects.create(person=person,type='WHATSAPP',raw_value=payload['alternate_phone'],normalized_value=normalize_phone(payload['alternate_phone']))
        if payload.get('heard_about_us'):activity(request,person,'How the visitor found us',payload['heard_about_us'],'NOTE')
        from crm.models import EducationRecord,TestScore
        for education in payload.get('education',[]):EducationRecord.objects.create(person=person,**education)
        for score in payload.get('test_scores',[]):TestScore.objects.create(person=person,**score)
        submission.outcome='CREATED'
        owner=None if payload.get('_paper_entry') else automatic_owner(qr.branch)
        start_sla(person,'ASSIGNMENT');start_sla(person,'FIRST_CONTACT')
        if owner:assign_owner(request,person,owner,'AUTO: round-robin')
        else:notify_managers(qr.branch,'INTAKE','Verified visitor awaiting assignment',person)
        refresh_next_action(person)
        queue_candidates=__import__('crm.services',fromlist=['queue_candidates']).queue_candidates
        queue_candidates(person,matches)
    submission.person=person;submission.status='PROCESSED';submission.save()
    if person:
        # Existing records keep identity/contact details; visitor payload stays isolated for review.
        ConsentRecord.objects.create(person=person,wording_version=payload.get('consent_version','visitor-v1'),method='WEB_FORM' if not submission.verified_by_staff_id else 'STAFF',recorded_by=request.user if request.user.is_authenticated else None,channels=payload.get('consent_channels',['CALL']),metadata={'submission_id':str(submission.pk),'ip_hash':submission.ip_hash})
        activity(request,person,'Registered via visitor form (verified)',f'Verification: {submission.verified_via}; submission {submission.pk}','INTAKE')
        audit(request,'INTAKE_PROCESSED',person,new={'submission_id':str(submission.pk),'outcome':submission.outcome})
        notify(person.owner,'INTAKE','Verified visitor registration received',person)
    if submission.outcome=='NEEDS_REVIEW':notify_managers(submission.qr.branch,'DUPLICATE','Verified intake needs review')
    return person
