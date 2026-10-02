import hashlib
import secrets
import os
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view,permission_classes,throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.authentication import SessionAuthentication
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.exceptions import PermissionDenied,ValidationError
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from crm.models import Person,Notification
from crm.permissions import scoped_people,profile
from crm.operations import required_reason,safe_date
from crm.services import audit
from admissions.models import Document,DocumentVersion
from admissions.api import validate_upload
from .models import PrivateLink,StudentRequest,StudentMessage


class LinkThrottle(SimpleRateThrottle):
    rate='10/min'
    def get_cache_key(self,request,view):
        if request.method=='GET':return None
        return 'student-link:'+self.get_ident(request)+':'+request.path


def access(request,lock=False):
    value=request.session.get('student_link')
    if not value:raise PermissionDenied('Open a current private link provided by your office.')
    qs=PrivateLink.objects.select_related('person')
    if lock:
        person_id=qs.filter(pk=value).values_list('person_id',flat=True).first()
        if person_id:Person.objects.select_for_update().filter(pk=person_id).exists()
        qs=qs.select_for_update(of=('self',))
    item=qs.filter(pk=value,active=True,expires_at__gt=timezone.now(),person__archived_at__isnull=True,person__merged_into__isnull=True).first()
    if not item:raise PermissionDenied('This private link is expired or revoked. Ask your office for a new link.')
    return item


def msg_data(m):return {'id':str(m.pk),'direction':m.direction,'body':m.body,'at':m.created_at,'request_id':str(m.request_id) if m.request_id else None}
def req_data(r):return {'id':str(r.pk),'title':r.title,'instructions':r.instructions,'due_at':r.due_at,'status':r.status}
def doc_data(d):return {'id':str(d.pk),'title':d.title,'status':'EXPIRED' if d.status=='VERIFIED' and d.expires_at and d.expires_at<=timezone.now() else d.status,'version':d.version,'shared':d.student_visible}


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@permission_classes([AllowAny])
@throttle_classes([LinkThrottle])
def entry(request):
    if request.method=='GET':
        try:item=access(request)
        except PermissionDenied:item=None
        return Response({'ready':bool(item),'csrf_token':get_token(request)})
    SessionAuthentication().enforce_csrf(request)
    if request.data.get('operation')=='close':
        request.session.pop('student_link',None)
        return Response({'ready':False,'csrf_token':get_token(request)})
    token=serializers.CharField(min_length=32,max_length=128).run_validation(request.data.get('token'))
    item=PrivateLink.objects.filter(token_hash=hashlib.sha256(token.encode()).hexdigest(),active=True,expires_at__gt=timezone.now(),person__archived_at__isnull=True,person__merged_into__isnull=True).first()
    if not item:raise ValidationError('This private link is invalid or expired. Ask your office for a new link.')
    request.session.cycle_key()
    request.session['student_link']=str(item.pk)
    request.session.set_expiry(min(3600,max(1,int((item.expires_at-timezone.now()).total_seconds()))))
    audit(request,'STUDENT_LINK_OPENED',item.person,new={'link_id':str(item.pk)})
    return Response({'ready':True,'csrf_token':get_token(request)})


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
@permission_classes([AllowAny])
def dashboard(request):
    item=access(request);person=item.person;apps=[]
    for app in person.applications.select_related('course_offering__course__university','course_offering__intake').order_by('-created_at'):
        offer=app.course_offering;departure=getattr(app,'predeparture',None);enrollment=getattr(app,'enrollment',None)
        apps.append({'id':str(app.pk),'ref':app.ref,'state':app.state,'course':offer.course.name,'university':offer.course.university.name,'intake':offer.intake.name,
            'offers':[{'type':o.type,'status':o.status,'expires_at':o.expires_at,'conditions':[{'title':c.title,'status':c.status} for c in o.tracked_conditions.all()]} for o in app.offers.all()],
            'visas':[{'ref':v.ref,'state':v.state,'appointment_at':v.appointment_at} for v in app.visa_cases.all()],
            'departure':{'checklist':departure.checklist,'flight_number':departure.flight_number,'departure_at':departure.departure_at,'arrival_at':departure.arrival_at,'accommodation':departure.accommodation,'airport_pickup':departure.airport_pickup} if departure else None,
            'enrolled_on':enrollment.enrolled_on if enrollment else None})
    return Response({'person':{'name':person.full_name,'ref':person.ref,'stage':person.stage,'state':person.student_state},'expires_at':item.expires_at,
        'applications':apps,'documents':[doc_data(d) for d in person.admission_documents.filter(student_visible=True)],
        'requests':[req_data(r) for r in person.student_requests.order_by('-created_at')],
        'deadlines':list(person.admission_deadlines.filter(status='OPEN').values('id','title','due_at'))})


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@permission_classes([AllowAny])
@transaction.atomic
def messages(request):
    item=access(request,lock=request.method=='POST');person=item.person
    if request.method=='GET':
        qs=StudentMessage.objects.filter(Q(person=person)|Q(person__merged_into=person)).order_by('-created_at','-id')
        offset=serializers.IntegerField(min_value=0).run_validation(request.query_params.get('offset',0))
        return Response({'count':qs.count(),'results':[msg_data(m) for m in qs[offset:offset+50]]})
    SessionAuthentication().enforce_csrf(request)
    if person.student_messages.filter(direction='STUDENT',created_at__gt=timezone.now()-timezone.timedelta(minutes=1)).count()>=10:raise ValidationError('Wait a minute before sending more messages.')
    body=serializers.CharField(max_length=4000).run_validation(request.data.get('body'));action=None
    if request.data.get('request_id'):
        action=get_object_or_404(StudentRequest.objects.select_for_update(),pk=serializers.UUIDField().run_validation(request.data['request_id']),person=person)
        if action.status not in ['OPEN','NEEDS_CHANGES']:raise ValidationError('This request has already been submitted or closed.')
        action.status='SUBMITTED';action.save(update_fields=['status'])
    msg=StudentMessage.objects.create(person=person,direction='STUDENT',body=body,access=item,request=action)
    if person.owner_id:Notification.objects.create(person=person,recipient_id=person.owner_id,type='STUDENT_MESSAGE',title='Student link message awaiting review')
    audit(request,'STUDENT_LINK_MESSAGE',person,new={'message_id':str(msg.pk),'link_id':str(item.pk)})
    return Response(msg_data(msg),status=201)


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@permission_classes([AllowAny])
@transaction.atomic
def document(request,pk):
    item=access(request,lock=request.method=='POST')
    doc=get_object_or_404(Document.objects.select_for_update(),pk=pk,person=item.person,student_visible=True)
    if request.method=='POST':
        SessionAuthentication().enforce_csrf(request)
        if doc.status not in ['REQUESTED','REJECTED','RESUBMISSION_REQUIRED','EXPIRED']:raise ValidationError('This document is complete or under review. Ask staff to request a replacement.')
        upload=request.FILES.get('file');raw,mime,filename=validate_upload(upload)
        doc.version+=1;doc.status='UPLOADED';doc.verified_by=None;doc.verified_at=None;doc.save()
        DocumentVersion.objects.create(document=doc,version=doc.version,filename=filename,mime=mime,data=raw,checksum=hashlib.sha256(raw).hexdigest(),uploaded_by=None)
        audit(request,'STUDENT_LINK_DOCUMENT',item.person,new={'document_id':str(doc.pk),'version':doc.version,'link_id':str(item.pk)})
        if item.person.owner_id:Notification.objects.create(person=item.person,recipient_id=item.person.owner_id,type='STUDENT_DOCUMENT',title='Student document awaiting staff verification')
        return Response(doc_data(doc))
    asset=get_object_or_404(DocumentVersion,document=doc,version=doc.version)
    response=HttpResponse(bytes(asset.data),content_type=asset.mime);response['Content-Disposition']='attachment; filename="document.'+{'application/pdf':'pdf','image/png':'png','image/jpeg':'jpg'}[asset.mime]+'"';response['X-Content-Type-Options']='nosniff';response['Cache-Control']='no-store'
    return response


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
def staff(request,pk):
    person=get_object_or_404(scoped_people(request.user,Person.objects.filter(archived_at__isnull=True,merged_into__isnull=True),'view' if request.method=='GET' else 'edit'),pk=pk)
    if request.method=='GET':
        qs=StudentMessage.objects.filter(Q(person=person)|Q(person__merged_into=person)).order_by('-created_at','-id');offset=serializers.IntegerField(min_value=0).run_validation(request.query_params.get('offset',0))
        return Response({'links':[{'id':str(l.pk),'active':l.active,'expires_at':l.expires_at} for l in PrivateLink.objects.filter(person=person).order_by('-created_at')[:20]],
            'documents':[doc_data(d) for d in person.admission_documents.all()],'requests':[req_data(r) for r in person.student_requests.order_by('-created_at')],
            'messages':[msg_data(m) for m in qs[offset:offset+50]],'message_count':qs.count()})
    if profile(request.user).role not in ['ADMIN','MANAGER','COUNSELOR']:raise PermissionDenied('Only responsible staff can manage student links and requests.')
    Person.objects.select_for_update().get(pk=person.pk);op=request.data.get('operation')
    if op=='link':
        days=serializers.IntegerField(min_value=1,max_value=7).run_validation(request.data.get('days',1));reason=required_reason(request.data);token=secrets.token_urlsafe(32)
        PrivateLink.objects.filter(person=person,active=True).update(active=False)
        link=PrivateLink.objects.create(person=person,token_hash=hashlib.sha256(token.encode()).hexdigest(),expires_at=timezone.now()+timezone.timedelta(days=days),created_by=request.user)
        audit(request,'STUDENT_LINK_CREATED',person,new={'link_id':str(link.pk),'expires_at':link.expires_at.isoformat(),'reason':reason})
        return Response({'url':os.getenv('PUBLIC_FORM_ORIGIN','http://127.0.0.1:3000').rstrip('/')+'/student#access='+token,'expires_at':link.expires_at},status=201)
    elif op=='revoke':
        reason=required_reason(request.data);PrivateLink.objects.filter(person=person,active=True).update(active=False);audit(request,'STUDENT_LINK_REVOKED',person,new={'reason':reason})
    elif op=='share':
        doc=get_object_or_404(Document.objects.select_for_update(),pk=serializers.UUIDField().run_validation(request.data.get('document_id')),person=person)
        doc.student_visible=serializers.BooleanField().run_validation(request.data.get('shared'));doc.save(update_fields=['student_visible']);audit(request,'STUDENT_DOCUMENT_VISIBILITY',person,new={'document_id':str(doc.pk),'shared':doc.student_visible,'reason':required_reason(request.data)})
    elif op=='request':
        action=StudentRequest.objects.create(person=person,title=serializers.CharField(max_length=160).run_validation(request.data.get('title')),instructions=serializers.CharField(max_length=4000).run_validation(request.data.get('instructions')),due_at=safe_date(request.data['due_at']) if request.data.get('due_at') else None,created_by=request.user)
        audit(request,'STUDENT_REQUEST_CREATED',person,new={'request_id':str(action.pk)})
    elif op=='review':
        action=get_object_or_404(StudentRequest.objects.select_for_update(),pk=serializers.UUIDField().run_validation(request.data.get('request_id')),person=person)
        state=serializers.ChoiceField(['COMPLETED','NEEDS_CHANGES','CANCELLED']).run_validation(request.data.get('status'));reason=required_reason(request.data)
        if action.status in ['COMPLETED','CANCELLED'] or (state!='CANCELLED' and action.status!='SUBMITTED'):raise ValidationError('Only submitted requests can be reviewed. Closed requests cannot change.')
        StudentMessage.objects.create(person=person,direction='OFFICE',sender=request.user,body=reason,request=action);action.status=state;action.save(update_fields=['status']);audit(request,'STUDENT_REQUEST_REVIEWED',person,new={'request_id':str(action.pk),'status':state,'reason':reason})
    elif op=='message':
        msg=StudentMessage.objects.create(person=person,direction='OFFICE',sender=request.user,body=serializers.CharField(max_length=4000).run_validation(request.data.get('body')));audit(request,'STUDENT_STAFF_MESSAGE',person,new={'message_id':str(msg.pk)})
    else:raise ValidationError('Choose a supported student link operation.')
    return Response({'ok':True})
