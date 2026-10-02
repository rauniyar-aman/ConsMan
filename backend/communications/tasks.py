from celery import shared_task
from .models import AutomationRule,WebhookReceipt,SocialLead,WorkerHeartbeat
from .dispatch import deliver,sweep
from .automation import run_rule
from .webhooks import process,fetch_lead


@shared_task(ignore_result=True)
def deliver_message(pk):return deliver(pk)


@shared_task(ignore_result=True)
def process_webhook(pk):
    try:process(pk)
    except Exception:
        from django.db.models import F
        WebhookReceipt.objects.filter(pk=pk).update(attempts=F('attempts')+1,error_code='WEBHOOK_PROCESS_FAILED')


@shared_task(ignore_result=True)
def retrieve_lead(pk):
    try:fetch_lead(pk)
    except Exception:
        from django.db.models import F
        SocialLead.objects.filter(pk=pk).update(attempts=F('attempts')+1,error_code='META_FETCH_FAILED')


@shared_task(ignore_result=True)
def communication_tick():
    from django.utils import timezone
    WorkerHeartbeat.objects.update_or_create(name='communications',defaults={'seen_at':timezone.now()})
    for pk in AutomationRule.objects.filter(enabled=True).values_list('pk',flat=True):run_rule(pk)
    for pk in WebhookReceipt.objects.filter(status='PENDING',attempts__lt=10).values_list('pk',flat=True)[:100]:process_webhook.delay(pk)
    for pk in SocialLead.objects.filter(status='PENDING',attempts__lt=5).values_list('pk',flat=True)[:100]:retrieve_lead.delay(pk)
    for pk in sweep():deliver_message.delay(str(pk))
