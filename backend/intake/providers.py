import hashlib
import hmac
from urllib.parse import quote
import requests
from django.conf import settings

class DeliveryFailure(Exception):
    pass

def digest(value):
    return hmac.new(settings.SECRET_KEY.encode(),str(value).encode(),hashlib.sha256).hexdigest()

class DisabledProvider:
    name='disabled'
    def send(self,phone,code,delivery):
        raise DeliveryFailure('PROVIDER_NOT_CONFIGURED')

class GatewayProvider:
    name='wa-akg'
    def send(self,phone,code,delivery):
        if not settings.WHATSAPP_GATEWAY_KEY or not settings.WHATSAPP_GATEWAY_SESSION:raise DeliveryFailure('PROVIDER_NOT_CONFIGURED')
        message=f'The Blessing Edu: your verification code is {code}. Never share this code. Ignore this if you did not request it.'
        delivery.content_hash=digest(message);delivery.save(update_fields=['content_hash'])
        jid=phone.lstrip('+')+'@s.whatsapp.net'
        url=f'{settings.WHATSAPP_GATEWAY_URL.rstrip("/")}/api/messages/{quote(settings.WHATSAPP_GATEWAY_SESSION,safe="")}/{quote(jid,safe="")}/send'
        try:
            response=requests.post(url,headers={'X-API-Key':settings.WHATSAPP_GATEWAY_KEY},json={'message':{'text':message}},timeout=(2,4),allow_redirects=False)
            if not response.ok or response.json().get('status') is not True:raise DeliveryFailure('GATEWAY_SEND_FAILED')
        except (requests.RequestException,ValueError):raise DeliveryFailure('GATEWAY_UNAVAILABLE')
        # Gateway currently returns no message ID; signed message.sent webhook binds it.
        return ''

class SmsHttpProvider:
    name='sms-http'
    def send(self,phone,code,delivery):
        if not settings.SMS_PROVIDER_URL or not settings.SMS_PROVIDER_KEY:raise DeliveryFailure('PROVIDER_NOT_CONFIGURED')
        try:
            response=requests.post(settings.SMS_PROVIDER_URL,headers={'Authorization':f'Bearer {settings.SMS_PROVIDER_KEY}'},json={'to':phone,'message':f'The Blessing Edu verification code: {code}. Never share this code. Ignore if not requested.'},timeout=(2,4),allow_redirects=False)
            if not response.ok:raise DeliveryFailure('SMS_SEND_FAILED')
            result=response.json()
            if not result.get('id'):raise DeliveryFailure('SMS_RESPONSE_INVALID')
            return str(result['id'])[:160]
        except (requests.RequestException,ValueError):raise DeliveryFailure('SMS_UNAVAILABLE')

def get_provider(channel):
    if channel=='WHATSAPP' and settings.WHATSAPP_GATEWAY_URL:return GatewayProvider()
    if channel=='SMS' and settings.SMS_PROVIDER_URL:return SmsHttpProvider()
    return DisabledProvider()
