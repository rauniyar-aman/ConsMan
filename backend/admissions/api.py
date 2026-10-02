import hashlib,io
from pathlib import Path
from datetime import timedelta
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q,Max
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError,PermissionDenied
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from PIL import Image
import pymupdf
from crm.models import Person
from crm.permissions import profile,scope,scoped_people
from crm.operations import required_reason,safe_date,require_fields
from crm.services import audit
from crm.idempotency import idempotent
from .models import *
from .serializers import CATALOG
from .services import *


def people(request,action='view'):
    return scoped_people(request.user,Person.objects.filter(archived_at__isnull=True),action)


def writable(request):
    if profile(request.user).role=='FRONTDESK' or scope(request.user,'edit')=='documents':raise PermissionDenied('Documentation staff can update assigned documents only.')


def person_for(request,value,action='view'):
    pk=serializers.UUIDField().run_validation(value)
    return get_object_or_404(people(request,action),pk=pk)


def apps(request,action='view'):
    return Application.objects.filter(person__in=people(request,action)).select_related('person__branch','owner','course_offering__course__university','course_offering__campus__university','course_offering__intake','workflow_template')


def get_app(request,pk,write=False):
    if write:writable(request)
    qs=apps(request,'edit' if write else 'view')
    if write:qs=qs.select_for_update(of=('self',))
    return get_object_or_404(qs,pk=pk)


def app_data(app):
    offering=app.course_offering
    return {'id':str(app.pk),'ref':app.ref,'person_id':str(app.person_id),'person_name':app.person.full_name,'person_ref':app.person.ref,'owner_id':app.owner_id,'owner_name':app.owner.get_full_name() or app.owner.username,'branch_id':app.person.branch_id,'state':app.state,'offering_id':offering.pk,'course':offering.course.name,'level':offering.course.level,'university':offering.campus.university.name,'campus':offering.campus.name,'destination':offering.campus.university.country,'intake':offering.intake.name,'start_date':offering.intake.start_date,'fee':str(offering.fee),'currency':offering.currency,'workflow_id':app.workflow_template_id,'workflow_version':app.workflow_template.version,'milestones':app.workflow_template.milestones,'enrollment_requirements':app.workflow_template.enrollment_requirements,'visa_requirements':app.workflow_template.visa_requirements,'notes':app.notes,'created_at':app.created_at,'updated_at':app.updated_at}


def doc_data(doc):
    expired=doc.status=='VERIFIED' and doc.expires_at and doc.expires_at<=timezone.now()
    return {'id':str(doc.pk),'person_id':str(doc.person_id),'application_id':str(doc.application_id) if doc.application_id else None,'type':doc.type,'title':doc.title,'required':doc.required,'status':'EXPIRED' if expired else doc.status,'expires_at':doc.expires_at,'verified_at':doc.verified_at,'verified_by_id':doc.verified_by_id,'version':doc.version,'versions':[{'id':v.pk,'version':v.version,'filename':v.filename,'mime':v.mime,'checksum':v.checksum,'uploaded_by_id':v.uploaded_by_id,'created_at':v.created_at} for v in doc.versions.all().order_by('-version')]}


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST','PATCH'])
@transaction.atomic
@idempotent()
def catalog(request,kind=None,pk=None):
    scope(request.user,'view')
    if request.method=='GET':
        if kind and kind not in CATALOG:raise ValidationError('Unknown catalogue type.')
        kinds=[kind] if kind else CATALOG
        return Response({k:CATALOG[k](CATALOG[k].Meta.model.objects.order_by('pk')[:1000],many=True).data for k in kinds})
    if profile(request.user).role!='ADMIN':raise PermissionDenied('Only administrators maintain admissions masters and workflow versions.')
    if kind not in CATALOG:raise ValidationError('Unknown catalogue type.')
    serializer_class=CATALOG[kind];model=serializer_class.Meta.model
    instance=get_object_or_404(model.objects.select_for_update(),pk=pk) if pk else None
    if request.method=='PATCH' and not instance:raise ValidationError('Select a catalogue record.')
    if instance and kind=='workflows':
        require_fields(request.data,['active'])
        active=serializers.BooleanField().run_validation(request.data.get('active'))
        instance.active=active;instance.save(update_fields=['active'])
        audit(request,'ADMISSION_WORKFLOW_AVAILABILITY',instance,new={'active':active})
        return Response(serializer_class(instance).data)
    serializer=serializer_class(instance,data=request.data,partial=bool(instance));serializer.is_valid(raise_exception=True)
    if kind=='workflows':
        destination=serializer.validated_data['destination']
        version=(WorkflowTemplate.objects.filter(destination=destination).aggregate(v=Max('version'))['v'] or 0)+1
        result=serializer.save(version=version,created_by=request.user)
    else:result=serializer.save()
    audit(request,'ADMISSION_MASTER_UPDATED' if instance else 'ADMISSION_MASTER_CREATED',result,new={'kind':kind})
    return Response(serializer_class(result).data,status=200 if instance else 201)


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
@idempotent()
def applications(request):
    if request.method=='GET':
        qs=apps(request)
        if request.query_params.get('person_id'):qs=qs.filter(person=person_for(request,request.query_params['person_id']))
        if request.query_params.get('state'):qs=qs.filter(state=request.query_params['state'])
        search=request.query_params.get('search','').strip()
        if search:qs=qs.filter(Q(ref__icontains=search)|Q(person__full_name__icontains=search)|Q(course_offering__course__name__icontains=search))
        try:offset=max(0,int(request.query_params.get('offset',0)))
        except ValueError:raise ValidationError('Invalid offset.')
        return Response({'count':qs.count(),'offset':offset,'results':[app_data(a) for a in qs.order_by('-created_at')[offset:offset+50]]})
    writable(request)
    person=person_for(request,request.data.get('person_id'),'edit')
    offering=get_object_or_404(CourseOffering.objects.select_related('campus__university','course','intake'),pk=serializers.IntegerField().run_validation(request.data.get('offering_id')),active=True)
    if offering.availability=='CLOSED' or not offering.course.active or not offering.campus.active or not offering.campus.university.active or not offering.intake.active:raise ValidationError('Choose an available course offering.')
    workflow=get_object_or_404(WorkflowTemplate,pk=serializers.IntegerField().run_validation(request.data.get('workflow_id')),active=True)
    if workflow.destination.casefold()!=offering.campus.university.country.casefold():raise ValidationError('Choose a workflow matching the destination.')
    owner=person.owner or request.user
    if not owner.is_active:raise ValidationError('Assign an active owner before creating an application.')
    app=Application.objects.create(ref=next_reference(),person=person,course_offering=offering,workflow_template=workflow,owner=owner,notes=serializers.CharField(max_length=5000,allow_blank=True).run_validation(request.data.get('notes','')),created_by=request.user)
    initialize_application(request,app)
    return Response(app_data(app),status=201)


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','PATCH'])
@transaction.atomic
@idempotent()
def application_detail(request,pk):
    app=get_app(request,pk,request.method=='PATCH')
    if request.method=='PATCH':
        require_fields(request.data,['notes','state','reason','offering_id','owner_id'])
        if 'notes' in request.data:
            notes=serializers.CharField(max_length=5000,allow_blank=True).run_validation(request.data['notes'])
            old=app.notes;app.notes=notes;app.save(update_fields=['notes','updated_at']);event(request,app,'NOTES_CHANGED',old={'notes':old},new={'notes':notes})
        if 'owner_id' in request.data:
            if profile(request.user).role not in ['ADMIN','MANAGER']:raise PermissionDenied('Only managers and administrators reassign application owners.')
            owner=get_object_or_404(User,pk=request.data['owner_id'],is_active=True,staff__branch_id=app.person.branch_id)
            old=app.owner_id;app.owner=owner;app.save(update_fields=['owner','updated_at']);event(request,app,'OWNER_CHANGED',old={'owner_id':old},new={'owner_id':owner.pk},reason=required_reason(request.data))
        if 'offering_id' in request.data:
            reason=required_reason(request.data)
            if app.state not in ['DRAFT','DOCUMENT_COLLECTION','DEFERRED']:raise ValidationError('Change the offering only for drafts, document collection or reviewed deferrals.')
            offering=get_object_or_404(CourseOffering.objects.select_related('campus__university'),pk=request.data['offering_id'],active=True)
            if offering.availability=='CLOSED' or not all([offering.course.active,offering.campus.active,offering.campus.university.active,offering.intake.active]) or offering.campus.university.country.casefold()!=app.workflow_template.destination.casefold():raise ValidationError('Choose an available offering in the same destination; create a new application for another destination.')
            old=app.course_offering_id;app.course_offering=offering
            if app.state=='DEFERRED':app.state='DRAFT'
            app.save();event(request,app,'OFFERING_CHANGED',old={'offering_id':old},new={'offering_id':offering.pk},reason=reason)
            for deadline in app.deadlines.filter(source='INTAKE',status='OPEN'):
                deadline.status='SUPERSEDED';deadline.save(update_fields=['status'])
            if offering.intake.application_deadline:Deadline.objects.create(person=app.person,application=app,type='APPLICATION',title='Revised application submission',due_at=offering.intake.application_deadline,source='INTAKE')
            from datetime import datetime,time
            Deadline.objects.create(person=app.person,application=app,type='COURSE_START',title='Revised course start',due_at=timezone.make_aware(datetime.combine(offering.intake.start_date,time(10))),source='INTAKE')
        if 'state' in request.data:transition(request,app,request.data['state'],str(request.data.get('reason',''))[:500])
    data=app_data(app)
    data.update(documents=[doc_data(d) for d in app.documents.prefetch_related('versions')],events=list(app.events.order_by('-created_at').values('id','type','actor_id','old','new','reason','created_at')),deadlines=list(app.deadlines.values()),blockers=list(app.person.admission_blockers.filter(Q(application=app)|Q(application__isnull=True)).values()),offers=list(app.offers.values()),tasks=[{'id':link.task_id,'title':link.task.title,'due_at':link.task.due_at,'status':link.task.status,'milestone':link.milestone} for link in app.generated_tasks.select_related('task')],ready=not incomplete_documents(app) and not blockers(app),can_edit=bool(profile(request.user).role in ['ADMIN','MANAGER','COUNSELOR']),can_documents=bool(profile(request.user).role in ['ADMIN','MANAGER','COUNSELOR','DOCS','FRONTDESK']))
    return Response(data)


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST','PATCH'])
@transaction.atomic
@idempotent()
def preferences(request,person_id):
    person=person_for(request,person_id,'view' if request.method=='GET' else 'edit')
    if request.method!='GET':writable(request)
    pref=StudentPreference.objects.filter(person=person).first()
    if request.method!='GET':
        if not pref:pref=StudentPreference(person=person)
        require_fields(request.data,['countries','levels','courses','intakes','maximum_fee','currency','academic_percent','notes'])
        for key in ['countries','levels']:
            if key in request.data:setattr(pref,key,serializers.ListField(child=serializers.CharField(max_length=80),max_length=30).run_validation(request.data[key]))
        for key in ['maximum_fee','academic_percent']:
            if key in request.data:
                value=serializers.DecimalField(max_digits=12,decimal_places=2,min_value=0,max_value=100 if key=='academic_percent' else None,allow_null=True).run_validation(request.data[key]);setattr(pref,key,value)
        if 'currency' in request.data:pref.currency=serializers.RegexField(r'^[A-Za-z]{3}$').run_validation(request.data['currency']).upper()
        if 'notes' in request.data:pref.notes=serializers.CharField(max_length=5000,allow_blank=True).run_validation(request.data['notes'])
        pref.save()
        for key,model in [('courses',Course),('intakes',Intake)]:
            if key in request.data:
                ids=serializers.ListField(child=serializers.IntegerField(),max_length=100).run_validation(request.data[key]);selected=model.objects.filter(pk__in=ids,active=True)
                if selected.count()!=len(set(ids)):raise ValidationError('Select active preference records.')
                getattr(pref,key).set(selected)
        audit(request,'ADMISSION_PREFERENCES_UPDATED',person)
    data={'person_id':str(person.pk),'countries':pref.countries if pref else person.preferred_countries,'levels':pref.levels if pref else person.study_levels,'courses':list(pref.courses.values_list('pk',flat=True)) if pref else [],'intakes':list(pref.intakes.values_list('pk',flat=True)) if pref else [],'maximum_fee':pref.maximum_fee if pref else None,'currency':pref.currency if pref else 'USD','academic_percent':pref.academic_percent if pref else None,'notes':pref.notes if pref else ''}
    offerings=CourseOffering.objects.filter(active=True,course__active=True,campus__active=True,campus__university__active=True,intake__active=True).exclude(availability='CLOSED').select_related('course','campus__university','intake').prefetch_related('scholarships').order_by('pk')[:500]
    data['matches']=matching(person,pref,offerings)
    return Response(data)


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
@idempotent()
def document_create(request):
    if request.method=='GET':
        person=person_for(request,request.query_params.get('person_id'))
        return Response({'results':[doc_data(d) for d in Document.objects.filter(person=person).prefetch_related('versions')]})
    person=person_for(request,request.data.get('person_id'),'edit')
    app=get_object_or_404(apps(request,'edit'),pk=request.data['application_id']) if request.data.get('application_id') else None
    if app and app.person_id!=person.pk:raise ValidationError('Document person must match the application.')
    if app and app.documents.filter(type=request.data.get('type')).exists():raise ValidationError('This document type is already requested for the application.')
    doc=Document.objects.create(person=person,application=app,type=serializers.CharField(max_length=80).run_validation(request.data.get('type')),title=serializers.CharField(max_length=160).run_validation(request.data.get('title')),required=serializers.BooleanField().run_validation(request.data.get('required',True)))
    audit(request,'ADMISSION_DOCUMENT_REQUESTED',person,new={'document_id':str(doc.pk),'application_id':str(app.pk) if app else None})
    if app:event(request,app,'DOCUMENT_REQUESTED',new={'document_id':str(doc.pk),'type':doc.type})
    return Response(doc_data(doc),status=201)


def get_document(request,pk,write=False):
    qs=Document.objects.filter(person__in=people(request,'edit' if write else 'view')).select_related('person__branch','application')
    if write:qs=qs.select_for_update(of=('self',))
    return get_object_or_404(qs,pk=pk)




def validate_upload(upload):
    if not upload:raise ValidationError('Upload a PDF, PNG or JPEG.')
    raw=upload.read();mime=''
    if raw.startswith(b'%PDF-'):
        try:
            with pymupdf.open(stream=raw,filetype='pdf') as pdf:
                if pdf.is_encrypted or not len(pdf) or pdf.embfile_count():raise ValueError()
            if b'/JavaScript' in raw or b'/JS' in raw or b'/Launch' in raw:raise ValueError()
            mime='application/pdf'
        except Exception:raise ValidationError('Upload a readable PDF without scripts, attachments or encryption.')
    else:
        try:
            image=Image.open(io.BytesIO(raw))
            if image.format not in ['PNG','JPEG'] or image.width*image.height>20000000:raise ValueError()
            mime='image/png' if image.format=='PNG' else 'image/jpeg';image.verify()
        except Exception:raise ValidationError('Upload a valid PNG, JPEG or PDF.')
    filename=Path(upload.name.replace('\\','/')).name[:160]
    return raw,mime,filename

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
@idempotent()
def document_batch_upload(request):
    person=person_for(request,request.data.get('person_id'),'edit')
    files=request.FILES.getlist('files')
    if not 1<=len(files)<=20:raise ValidationError('Choose between 1 and 20 files.')
    validated=[validate_upload(upload) for upload in files]
    documents=[]
    for raw,mime,filename in validated:
        doc=Document.objects.create(person=person,type='Unclassified',title=filename,required=False,status='UPLOADED',version=1)
        checksum=hashlib.sha256(raw).hexdigest()
        DocumentVersion.objects.create(document=doc,version=1,filename=filename,mime=mime,data=raw,checksum=checksum,uploaded_by=request.user)
        audit(request,'ADMISSION_DOCUMENT_UPLOADED',person,new={'document_id':str(doc.pk),'version':1,'checksum':checksum})
        documents.append(doc_data(doc))
    return Response({'results':documents},status=201)


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','PATCH','POST'])
@transaction.atomic
def document_detail(request,pk):
    doc=get_document(request,pk,request.method!='GET')
    if request.method=='POST':
        raw,mime,filename=validate_upload(request.FILES.get('file'))
        doc.version+=1;doc.status='UPLOADED';doc.verified_by=None;doc.verified_at=None;doc.save()
        DocumentVersion.objects.create(document=doc,version=doc.version,filename=filename,mime=mime,data=raw,checksum=hashlib.sha256(raw).hexdigest(),uploaded_by=request.user)
        audit(request,'ADMISSION_DOCUMENT_UPLOADED',doc.person,new={'document_id':str(doc.pk),'version':doc.version,'checksum':hashlib.sha256(raw).hexdigest()})
        if doc.application:event(request,doc.application,'DOCUMENT_UPLOADED',new={'document_id':str(doc.pk),'version':doc.version})
    elif request.method=='PATCH':
        if set(request.data).issubset({'type','title'}) and request.data:
            old={'type':doc.type,'title':doc.title}
            new_type=serializers.CharField(max_length=80).run_validation(request.data.get('type',doc.type))
            new_title=serializers.CharField(max_length=160).run_validation(request.data.get('title',doc.title))
            if doc.application_id and Document.objects.filter(application_id=doc.application_id,type=new_type).exclude(pk=doc.pk).exists():raise ValidationError('This document type already exists for the application.')
            doc.type=new_type;doc.title=new_title
            if old!={'type':doc.type,'title':doc.title}:
                if doc.version:doc.status='UPLOADED';doc.verified_by=None;doc.verified_at=None
                doc.save()
                audit(request,'ADMISSION_DOCUMENT_RENAMED',doc.person,old=old,new={'document_id':str(doc.pk),'type':doc.type,'title':doc.title})
            return Response(doc_data(doc))
        if profile(request.user).role=='FRONTDESK':raise PermissionDenied('Frontdesk officers upload documents; counselors review them.')
        require_fields(request.data,['status','expires_at','reason'])
        state=request.data.get('status',doc.status)
        if state not in DOCUMENT_STATES:raise ValidationError('Unsupported document status.')
        allowed={'REQUESTED':['WAIVED','NOT_APPLICABLE'],'UPLOADED':['UNDER_REVIEW','WAIVED','NOT_APPLICABLE'],'UNDER_REVIEW':['VERIFIED','REJECTED','WAIVED','NOT_APPLICABLE'],'REJECTED':['RESUBMISSION_REQUIRED','WAIVED','NOT_APPLICABLE'],'RESUBMISSION_REQUIRED':['WAIVED','NOT_APPLICABLE'],'VERIFIED':['EXPIRED','UNDER_REVIEW','WAIVED','NOT_APPLICABLE'],'EXPIRED':['RESUBMISSION_REQUIRED','WAIVED','NOT_APPLICABLE'],'WAIVED':['REQUESTED'],'NOT_APPLICABLE':['REQUESTED']}
        if state!=doc.status and state not in allowed.get(doc.status,[]):raise ValidationError('Invalid document state transition.')
        reason=required_reason(request.data) if state in ['REJECTED','WAIVED','NOT_APPLICABLE','REQUESTED'] and state!=doc.status else str(request.data.get('reason',''))[:500]
        if 'expires_at' in request.data:doc.expires_at=safe_date(request.data['expires_at']) if request.data['expires_at'] else None
        if state=='VERIFIED':
            if not doc.version:raise ValidationError('Upload a document before verification.')
            if doc.expires_at and doc.expires_at<=timezone.now():raise ValidationError('Cannot verify an expired document.')
            doc.verified_by=request.user;doc.verified_at=timezone.now()
        else:doc.verified_by=None;doc.verified_at=None
        old=doc.status;doc.status=state;doc.save()
        audit(request,'ADMISSION_DOCUMENT_REVIEWED',doc.person,old={'status':old},new={'document_id':str(doc.pk),'status':state,'reason':reason})
        if doc.application:event(request,doc.application,'DOCUMENT_REVIEWED',old={'status':old},new={'document_id':str(doc.pk),'status':state},reason=reason)
    return Response(doc_data(doc))


@extend_schema(responses=OpenApiTypes.BINARY)
@api_view(['GET'])
def document_download(request,pk,version):
    doc=get_document(request,pk)
    asset=get_object_or_404(DocumentVersion,document=doc,version=version)
    raw=bytes(asset.data)
    if hashlib.sha256(raw).hexdigest()!=asset.checksum:raise ValidationError('Document integrity check failed.')
    response=HttpResponse(raw,content_type=asset.mime)
    from django.utils.http import content_disposition_header
    response['Content-Disposition']=content_disposition_header(True,asset.filename)
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    audit(request,'ADMISSION_DOCUMENT_DOWNLOADED',doc.person,new={'document_id':str(doc.pk),'version':version})
    return response


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST','PATCH'])
@transaction.atomic
@idempotent()
def deadlines(request,pk=None):
    if request.method=='GET':
        qs=Deadline.objects.filter(person__in=people(request),status='OPEN').select_related('person','application')
        if request.query_params.get('application_id'):qs=qs.filter(application=get_app(request,request.query_params['application_id']))
        if request.query_params.get('person_id'):qs=qs.filter(person=person_for(request,request.query_params['person_id']))
        return Response({'results':[{'id':d.pk,'person_id':str(d.person_id),'person_name':d.person.full_name,'application_id':str(d.application_id) if d.application_id else None,'application_ref':d.application.ref if d.application_id else '', 'type':d.type,'title':d.title,'due_at':d.due_at,'source':d.source,'status':d.status,'overdue':d.due_at<timezone.now()} for d in qs.order_by('due_at')[:200]]})
    writable(request)
    if pk:
        d=get_object_or_404(Deadline.objects.select_for_update().filter(person__in=people(request,'edit')),pk=pk)
        require_fields(request.data,['due_at','status','reason'])
        reason=required_reason(request.data)
        if 'due_at' in request.data:d.due_at=safe_date(request.data['due_at'])
        if 'status' in request.data:
            if request.data['status'] not in ['OPEN','COMPLETED','CANCELLED']:raise ValidationError('Invalid deadline status.')
            d.status=request.data['status'];d.completed_at=timezone.now() if d.status=='COMPLETED' else None
        d.save()
    else:
        person=person_for(request,request.data.get('person_id'),'edit');app=get_app(request,request.data['application_id'],True) if request.data.get('application_id') else None
        if app and app.person_id!=person.pk:raise ValidationError('Deadline person must match the application.')
        kind=request.data.get('type','APPLICATION')
        if kind not in DEADLINE_TYPES:raise ValidationError('Invalid deadline type.')
        d=Deadline.objects.create(person=person,application=app,type=kind,title=serializers.CharField(max_length=160).run_validation(request.data.get('title')),due_at=safe_date(request.data.get('due_at')));reason=''
    audit(request,'ADMISSION_DEADLINE_UPDATED',d.person,new={'deadline_id':d.pk,'due_at':d.due_at.isoformat(),'status':d.status,'reason':reason})
    if d.application:event(request,d.application,'DEADLINE_UPDATED',new={'deadline_id':d.pk,'status':d.status},reason=reason)
    return Response({'id':d.pk,'status':d.status})


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST','PATCH'])
@transaction.atomic
@idempotent()
def blocker_queue(request,pk=None):
    if request.method=='GET':
        qs=Blocker.objects.filter(person__in=people(request),resolved_at__isnull=True)
        if request.query_params.get('person_id'):qs=qs.filter(person=person_for(request,request.query_params['person_id']))
        if request.query_params.get('application_id'):
            app=get_app(request,request.query_params['application_id']);qs=qs.filter(Q(application=app)|Q(application__isnull=True,person=app.person))
        return Response({'results':list(qs.order_by('created_at').values('id','person_id','person__full_name','application_id','type','description','assigned_to_id','created_at')[:200])})
    writable(request)
    if pk:
        blocker=get_object_or_404(Blocker.objects.select_for_update().filter(person__in=people(request,'edit')),pk=pk)
        if blocker.resolved_at:raise ValidationError('Blocker is already resolved.')
        blocker.resolution=required_reason(request.data);blocker.resolved_at=timezone.now();blocker.save()
    else:
        person=person_for(request,request.data.get('person_id'),'edit');app=get_app(request,request.data['application_id'],True) if request.data.get('application_id') else None
        if app and app.person_id!=person.pk:raise ValidationError('Blocker person must match the application.')
        assignee=get_object_or_404(User,pk=request.data.get('assigned_to_id',request.user.pk),is_active=True,staff__branch_id=person.branch_id)
        blocker=Blocker.objects.create(person=person,application=app,type=serializers.CharField(max_length=60).run_validation(request.data.get('type','OTHER')),description=serializers.CharField(max_length=2000).run_validation(request.data.get('description')),assigned_to=assignee,created_by=request.user)
    audit(request,'ADMISSION_BLOCKER_UPDATED',blocker.person,new={'blocker_id':blocker.pk,'resolved':bool(blocker.resolved_at),'resolution':blocker.resolution})
    if blocker.application:event(request,blocker.application,'BLOCKER_UPDATED',new={'blocker_id':blocker.pk,'resolved':bool(blocker.resolved_at)},reason=blocker.resolution)
    return Response({'id':blocker.pk,'resolved_at':blocker.resolved_at})


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST','PATCH'])
@transaction.atomic
@idempotent()
def offers(request,pk,offer_id=None):
    app=get_app(request,pk,True)
    if offer_id:
        offer=get_object_or_404(OfferReceipt,pk=offer_id,application=app)
        if request.data.get('status') not in ['DECLINED','EXPIRED']:raise ValidationError('Offer acceptance and enrollment are enabled in Phase 3.')
        reason=required_reason(request.data);offer.status=request.data['status'];offer.save(update_fields=['status'])
        transition(request,app,'OFFER_DECLINED' if offer.status=='DECLINED' else 'OFFER_EXPIRED',reason)
        event(request,app,'OFFER_STATUS',new={'offer_id':offer.pk,'status':offer.status},reason=reason)
    else:
        kind=request.data.get('type','CONDITIONAL')
        if kind not in ['CONDITIONAL','UNCONDITIONAL']:raise ValidationError('Choose conditional or unconditional.')
        conditions=serializers.ListField(child=serializers.CharField(max_length=500),max_length=50).run_validation(request.data.get('conditions',[]))
        expires=safe_date(request.data['expires_at']) if request.data.get('expires_at') else None
        if expires and expires<=timezone.now():raise ValidationError('Offer expiry must be in the future.')
        document=get_object_or_404(app.documents,pk=request.data['document_id']) if request.data.get('document_id') else None
        offer=OfferReceipt.objects.create(application=app,type=kind,conditions=conditions,expires_at=expires,received_at=timezone.now(),reference=serializers.CharField(max_length=120,allow_blank=True).run_validation(request.data.get('reference','')),document=document)
        from progression.models import OfferCondition
        OfferCondition.objects.bulk_create([OfferCondition(offer=offer,title=title) for title in conditions])
        if expires:Deadline.objects.create(person=app.person,application=app,type='OFFER_EXPIRY',title='Offer expires',due_at=expires,source='OFFER')
        transition(request,app,'OFFER_RECEIVED',str(request.data.get('reason','Offer received from institution'))[:500])
        event(request,app,'OFFER_RECEIVED',new={'offer_id':offer.pk,'type':kind,'conditions':conditions})
    return Response({'id':offer.pk,'status':offer.status},status=200 if offer_id else 201)


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
def dashboard(request):
    permitted=people(request);applications=Application.objects.filter(person__in=permitted)
    now=timezone.now()
    return Response({'applications':applications.count(),'states':[{'state':s,'count':applications.filter(state=s).count()} for s in PHASE_TWO_STATES+TERMINAL_STATES],'open_blockers':Blocker.objects.filter(person__in=permitted,resolved_at__isnull=True).count(),'overdue_deadlines':Deadline.objects.filter(person__in=permitted,status='OPEN',due_at__lt=now).count(),'upcoming_deadlines':Deadline.objects.filter(person__in=permitted,status='OPEN',due_at__gte=now,due_at__lte=now+timedelta(days=7)).count(),'documents_for_review':Document.objects.filter(person__in=permitted,status__in=['UPLOADED','UNDER_REVIEW']).count()})
