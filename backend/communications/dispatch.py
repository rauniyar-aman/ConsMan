from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum
from crm.models import Person
from .models import OutboundMessage,MessageEvent
from .services import eligible
from .providers import configured,send,SendError


def event(message,status,detail=''):
    message.status=status;message.error_code=detail
    message.save(update_fields=['status','error_code','provider_id','attempts','claimed_at','scheduled_at'])
    MessageEvent.objects.create(message=message,status=status,detail=detail,estimated_cost_minor=getattr(message,'_estimated_cost_minor',0) if status=='SENDING' else 0)


def blocked(message,code):
    if message.error_code!=code:
        message.error_code=code;message.save(update_fields=['error_code'])
    return code


def deliver(pk):
    with transaction.atomic():
        person_id=OutboundMessage.objects.values_list('person_id',flat=True).get(pk=pk)
        Person.objects.select_for_update().get(pk=person_id)
        message=OutboundMessage.objects.select_for_update().select_related('person','contact').get(pk=pk)
        if message.status!='QUEUED' or message.scheduled_at>timezone.now():return message.status
        if not eligible(message):event(message,'CANCELLED','CONSENT_OR_CONTACT_CHANGED');return message.status
        if not configured(message.channel):return blocked(message,'PROVIDER_NOT_CONFIGURED')
        from progression.models import JourneyCounter
        lock,_=JourneyCounter.objects.get_or_create(kind='MSG_RATE',year=0)
        JourneyCounter.objects.select_for_update().get(pk=lock.pk)
        if MessageEvent.objects.filter(status='SENDING',created_at__gte=timezone.now()-timedelta(hours=1)).count()>=settings.COMMUNICATION_HOURLY_LIMIT:return blocked(message,'HOURLY_LIMIT')
        if MessageEvent.objects.filter(status='SENDING',created_at__date=timezone.localdate()).count()>=settings.COMMUNICATION_DAILY_LIMIT:return blocked(message,'DAILY_LIMIT')
        from intake.models import MessageDelivery
        month=timezone.localdate().replace(day=1)
        cost=getattr(settings,message.channel+'_MESSAGE_COST_MINOR',0)
        if message.channel=='SMS':
            import math
            cost*=max(1,math.ceil(len(message.body.encode('utf-16-le'))/2/67))
        budget=getattr(settings,'COMMUNICATION_'+message.channel+'_MONTHLY_BUDGET_MINOR',0)
        reserved=MessageEvent.objects.filter(message__channel=message.channel,status='SENDING',created_at__date__gte=month).aggregate(t=Sum('estimated_cost_minor'))['t'] or 0
        otp=MessageDelivery.objects.filter(channel=message.channel,created_at__date__gte=month).aggregate(t=Sum('cost_minor'))['t'] or 0
        if reserved+otp+cost>budget:return blocked(message,'MONTHLY_BUDGET')
        message._estimated_cost_minor=cost
        message.attempts+=1;message.claimed_at=timezone.now();event(message,'SENDING')
    # Never hold a database transaction open during network I/O. Consent withdrawal
    # cancels unclaimed messages; a message already submitted may still arrive.
    try:
        provider_id=send(message)
        status='SENT';code=''
    except SendError as failure:
        provider_id='';code=failure.code
        status='UNKNOWN' if failure.uncertain else 'QUEUED' if failure.retryable and message.attempts<3 else 'FAILED'
    with transaction.atomic():
        current=OutboundMessage.objects.select_for_update().get(pk=pk)
        current.provider_id=current.provider_id or provider_id
        if status=='QUEUED':current.scheduled_at=timezone.now()+timedelta(minutes=2**current.attempts)
        if current.status in ['SENDING','UNKNOWN']:
            event(current,status,code)
        else:current.save(update_fields=['provider_id'])
    return status


def sweep():
    cutoff=timezone.now()-timedelta(minutes=5)
    for pk in OutboundMessage.objects.filter(status='SENDING',claimed_at__lt=cutoff).values_list('pk',flat=True)[:100]:
        with transaction.atomic():
            obj=OutboundMessage.objects.select_for_update().get(pk=pk)
            if obj.status=='SENDING' and obj.claimed_at<cutoff:event(obj,'UNKNOWN','WORKER_INTERRUPTED')
    channels=[c for c in ['EMAIL','SMS','WHATSAPP'] if configured(c)]
    return list(OutboundMessage.objects.filter(status='QUEUED',channel__in=channels,scheduled_at__lte=timezone.now()).order_by('scheduled_at').values_list('pk',flat=True)[:100])
