import base64
import hashlib
import time
import pyotp
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import transaction
from django.middleware.csrf import get_token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.authentication import SessionAuthentication
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from .models import MfaDevice, AuditEvent

def cipher():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()))

def pending_user(request):
    SessionAuthentication().enforce_csrf(request)
    if request.user.is_authenticated:
        return request.user
    uid=request.session.get('mfa_pending')
    if not uid or request.session.get('mfa_until',0)<time.time() or request.session.get('mfa_attempts',0)>=5:
        raise PermissionDenied('Sign in again to continue verification.')
    return User.objects.get(pk=uid,is_active=True)

def require_recent_password(request):
    if request.session.get('password_verified_at',0)<time.time()-300:
        raise PermissionDenied('Sign in again before configuring MFA.')

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@permission_classes([AllowAny])
def mfa_setup(request):
    user=pending_user(request)
    require_recent_password(request)
    device,_=MfaDevice.objects.get_or_create(user=user,defaults={'encrypted_secret':cipher().encrypt(pyotp.random_base32().encode()).decode()})
    if device.confirmed:
        raise ValidationError('MFA is already enrolled. Contact an administrator for a reviewed reset.')
    secret=cipher().decrypt(device.encrypted_secret.encode()).decode()
    return Response({'secret':secret,'uri':pyotp.TOTP(secret).provisioning_uri(user.username,issuer_name='ConsMan')})

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@permission_classes([AllowAny])
def mfa_verify(request):
    user=pending_user(request)
    require_recent_password(request)
    request.session['mfa_attempts']=request.session.get('mfa_attempts',0)+1
    request.session.save()
    with transaction.atomic():
        device=MfaDevice.objects.select_for_update().filter(user=user).first()
        code=str(request.data.get('code',''))
        if not device or len(code)!=6 or not code.isdigit():
            raise ValidationError({'code':'Enter a valid six-digit code.'})
        totp=pyotp.TOTP(cipher().decrypt(device.encrypted_secret.encode()).decode())
        counter=int(time.time())//30
        match=next((c for c in [counter-1,counter,counter+1] if c>device.last_counter and totp.at(c*30)==code),None)
        if match is None:
            raise ValidationError({'code':'Invalid, expired, or already used code.'})
        enrolled=not device.confirmed
        device.confirmed=True;device.last_counter=match;device.save()
        AuditEvent.objects.create(actor=user,action='MFA_ENROLLED' if enrolled else 'MFA_LOGIN',object_id=str(user.pk),request_id=request.request_id)
    login(request,user,backend='django.contrib.auth.backends.ModelBackend')
    for key in ['mfa_pending','mfa_until','mfa_attempts']:
        request.session.pop(key,None)
    return Response({'ok':True,'csrf_token':get_token(request)})
