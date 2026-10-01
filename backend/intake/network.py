import ipaddress
from django.conf import settings

def client_ip(request):
    remote=request.META.get('REMOTE_ADDR','')
    trusted=[ipaddress.ip_network(value.strip()) for value in settings.TRUSTED_PROXY_IPS if value.strip()]
    def is_trusted(value):
        try:return any(ipaddress.ip_address(value) in network for network in trusted)
        except ValueError:return False
    if not is_trusted(remote):return remote or 'unknown'
    forwarded=request.headers.get('X-Forwarded-For','').split(',')
    for value in reversed(forwarded):
        value=value.strip()
        try:ipaddress.ip_address(value)
        except ValueError:continue
        if not is_trusted(value):return value
    return remote or 'unknown'
