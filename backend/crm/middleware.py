import uuid
import json
import logging
import time

class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = str(uuid.uuid4())
        started=time.perf_counter()
        response = self.get_response(request)
        response['X-Request-ID'] = request.request_id
        logging.getLogger('consman.requests').info(json.dumps({'request_id':request.request_id,'method':request.method,'route':getattr(getattr(request,'resolver_match',None),'route','unmatched'),'status':response.status_code,'duration_ms':round((time.perf_counter()-started)*1000,1)}))
        return response
