import os
from urllib.parse import quote
import requests
from django.conf import settings
from django.core.mail import EmailMessage


class SendError(Exception):
    def __init__(self, code, uncertain=False, retryable=False):
        self.code=code;self.uncertain=uncertain;self.retryable=retryable


def configured(channel):
    if channel=='EMAIL':return bool(os.getenv('EMAIL_HOST') and os.getenv('DEFAULT_FROM_EMAIL'))
    if channel=='WHATSAPP':return bool(settings.WHATSAPP_GATEWAY_URL and settings.WHATSAPP_GATEWAY_KEY and settings.WHATSAPP_GATEWAY_SESSION)
    if channel=='SMS':return bool(settings.SMS_PROVIDER_URL and settings.SMS_PROVIDER_KEY)
    return False


def send(message):
    if not configured(message.channel):raise SendError('PROVIDER_NOT_CONFIGURED')
    if message.channel=='EMAIL':
        try:
            mail=EmailMessage(message.subject,message.body,settings.DEFAULT_FROM_EMAIL,[message.recipient],headers={'Message-ID':f'<{message.pk}@consman.rauniyaraman.com.np>','X-ConsMan-Message-ID':str(message.pk)})
            if mail.send()!=1:raise SendError('EMAIL_NOT_ACCEPTED',uncertain=True)
            return str(message.pk)
        except SendError:raise
        except Exception:raise SendError('EMAIL_OUTCOME_UNKNOWN',uncertain=True)
    if message.channel=='WHATSAPP':
        jid=message.recipient.lstrip('+')+'@s.whatsapp.net'
        url=f'{settings.WHATSAPP_GATEWAY_URL.rstrip("/")}/api/messages/{quote(settings.WHATSAPP_GATEWAY_SESSION,safe="")}/{quote(jid,safe="")}/send'
        headers={'X-API-Key':settings.WHATSAPP_GATEWAY_KEY};payload={'message':{'text':message.body}}
    else:
        url=settings.SMS_PROVIDER_URL;headers={'Authorization':f'Bearer {settings.SMS_PROVIDER_KEY}','Idempotency-Key':str(message.pk)};payload={'to':message.recipient,'message':message.body,'client_reference':str(message.pk)}
    try:
        response=requests.post(url,headers=headers,json=payload,timeout=(3,10),allow_redirects=False)
        if response.status_code==429:raise SendError('PROVIDER_RATE_LIMIT',retryable=True)
        if response.status_code>=500:raise SendError('PROVIDER_OUTCOME_UNKNOWN',uncertain=True)
        if not response.ok:raise SendError('PROVIDER_REJECTED')
        result=response.json()
        if message.channel=='WHATSAPP':
            if result.get('status') is not True:raise SendError('GATEWAY_REJECTED')
            return str(result.get('id',''))[:200]
        if not result.get('id'):raise SendError('SMS_OUTCOME_UNKNOWN',uncertain=True)
        return str(result['id'])[:200]
    except requests.ConnectTimeout:raise SendError('PROVIDER_CONNECT_TIMEOUT',retryable=True)
    except (requests.RequestException,ValueError):raise SendError('PROVIDER_OUTCOME_UNKNOWN',uncertain=True)
