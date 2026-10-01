import hashlib
import json
from functools import wraps
from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from .models import IdempotencyRecord

def idempotent(required=False):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            request = args[1] if hasattr(args[0], 'get_permissions') else args[0]
            if request.method in ['GET','HEAD','OPTIONS']:return fn(*args,**kwargs)
            key = request.headers.get('Idempotency-Key','').strip()
            if not key:
                if required:
                    raise ValidationError({'idempotency_key':'An Idempotency-Key header is required.'})
                return fn(*args, **kwargs)
            if len(key)>160:
                raise ValidationError({'idempotency_key':'Maximum length is 160 characters.'})
            identity = request.user.pk if request.user.is_authenticated else 'public'
            write_scope = f'{identity}:{request.path}'[:200]
            digest = hashlib.sha256(json.dumps(request.data,sort_keys=True,default=str).encode()).hexdigest()
            with transaction.atomic():
                record,_ = IdempotencyRecord.objects.get_or_create(key=key,scope=write_scope,defaults={'request_hash':digest})
                record = IdempotencyRecord.objects.select_for_update().get(pk=record.pk)
                if record.request_hash != digest:
                    raise ValidationError({'idempotency_key':'This key was already used with different data.'})
                if record.response is not None:
                    return Response(record.response,status=record.status_code)
                response=fn(*args,**kwargs)
                record.response=json.loads(json.dumps(response.data,default=str))
                record.status_code=response.status_code
                record.save(update_fields=['response','status_code'])
                return response
        return wrapped
    return decorate
