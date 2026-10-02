import csv
import io
import uuid
from datetime import timedelta
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from .models import *
from .permissions import scope, profile, scoped_people, branch_scope, MATRIX
from .services import activity, audit, assign_owner, normalize_phone, duplicate_signals, queue_candidates, notify, notify_managers, refresh_next_action
from .calendar import start_sla, satisfy
from .idempotency import idempotent

def object_person(request,pk,action='view',lock=False):
    people=scoped_people(request.user,Person.objects.filter(archived_at__isnull=True),action)
    if lock:people=people.select_for_update(of=('self',))
    return get_object_or_404(people,pk=pk)

def required_reason(data):
    reason=str(data.get('reason','')).strip()
    if not reason or len(reason)>500:raise ValidationError({'reason':'A reason of 1–500 characters is required.'})
    return reason

def require_fields(data,allowed):
    unknown=set(data)-set(allowed)
    if unknown:raise ValidationError({key:'Unsupported field.' for key in unknown})

def safe_date(value):
    try:
        result=parse_datetime(str(value))
        if not result or timezone.is_naive(result):raise ValueError()
        return result
    except (ValueError,TypeError):raise ValidationError('Enter an ISO date/time with a time-zone offset.')

class EditPersonSerializer(serializers.ModelSerializer):
    class Meta:
        model=Person
        fields=['full_name','dob','address','guardian_name','guardian_contact','preferred_country','preferred_countries','study_levels','preferred_course','preferred_intake','preferred_university','highest_education','work_experience','previous_visa_refusal','best_contact_method','source','campaign','source_detail','tags']
        extra_kwargs={name:{'required':False} for name in fields}
    def validate(self,data):
        source=data.get('source',self.instance.source)
        campaign=data.get('campaign',self.instance.campaign)
        detail=data.get('source_detail',self.instance.source_detail)
        if campaign and campaign.source_id!=source.pk:raise ValidationError({'campaign':'Campaign must belong to the selected source.'})
        if detail and (not campaign or detail.campaign_id!=campaign.pk):raise ValidationError({'source_detail':'Source detail must belong to the campaign.'})
        for field in ['preferred_countries','study_levels']:
            if field in data and (not isinstance(data[field],list) or len(data[field])>20 or any(not isinstance(x,str) or len(x)>80 for x in data[field])):raise ValidationError({field:'Use a list of up to 20 short strings.'})
        return data

@extend_schema(request=EditPersonSerializer,responses=OpenApiTypes.OBJECT)
@api_view(['PATCH'])
@transaction.atomic
def edit_person(request,pk):
    person=object_person(request,pk,'edit',True)
    if scope(request.user,'edit')=='documents':
        require_fields(request.data,['highest_education'])
    require_fields(request.data,EditPersonSerializer.Meta.fields)
    old={key:str(getattr(person,key,'')) for key in request.data if key!='tags'}
    serializer=EditPersonSerializer(person,data=request.data,partial=True)
    serializer.is_valid(raise_exception=True);serializer.save()
    activity(request,person,'Profile updated',', '.join(request.data.keys()))
    audit(request,'PERSON_EDITED',person,old,{key:str(value) for key,value in request.data.items()})
    contacts=person.contacts.all()
    matches=duplicate_signals(next((c.normalized_value for c in contacts if c.type=='PHONE'),''),next((c.normalized_value for c in contacts if c.type=='EMAIL'),''),person.full_name,person.dob,person.pk)
    queue_candidates(person,matches)
    return Response({'ok':True})

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
def lifecycle(request,pk):
    person=object_person(request,pk,'convert',True)
    require_fields(request.data,['status','temperature','reason','lost_reason_id','hold_until','student_state','revert_to_lead'])
    old={'stage':person.stage,'status':person.lead_status,'temperature':person.temperature,'student_state':person.student_state}
    reason=str(request.data.get('reason','')).strip()
    if request.data.get('revert_to_lead'):
        scope(request.user,'reassign');required_reason(request.data)
        if person.stage!='STUDENT':raise ValidationError('Only students can revert to lead.')
        person.stage='LEAD';person.lead_status='CONTACTED';person.student_state='';person.converted_at=None
    elif person.stage=='STUDENT':
        state=request.data.get('student_state')
        if state and state not in ['ACTIVE','ENROLLED','DEFERRED','WITHDRAWN','ARCHIVED']:raise ValidationError('Invalid student state.')
        if 'status' in request.data:raise ValidationError('Converted lead status is frozen.')
        if state:person.student_state=state
    else:
        target=request.data.get('status',person.lead_status)
        if target=='CONVERTED':raise ValidationError('Use the explicit conversion action.')
        if target not in Person.Status.values:raise ValidationError('Invalid lead status.')
        chain=['NEW','CONTACTED','COUNSELING','INTERESTED','DOCUMENT_COLLECTION','APPLICATION_READY']
        if target in chain and person.lead_status in chain and (chain.index(target)<chain.index(person.lead_status) or chain.index(target)>chain.index(person.lead_status)+1):required_reason(request.data)
        if person.lead_status=='LOST' and target!='LOST':required_reason(request.data)
        person.lost_reason=None;person.hold_until=None
        if target=='LOST':
            person.lost_reason=get_object_or_404(LostReason,pk=request.data.get('lost_reason_id'),active=True)
            if person.lost_reason.note_required:required_reason(request.data)
        if target=='ON_HOLD':
            person.hold_until=safe_date(request.data.get('hold_until'))
            if person.hold_until<=timezone.now():raise ValidationError('Resume date must be in the future.')
            FollowUp.objects.create(person=person,owner=person.owner,subject='Resume on-hold lead',due_at=person.hold_until)
        person.lead_status=target
    if 'temperature' in request.data:
        if request.data['temperature'] not in ['HOT','WARM','COLD']:raise ValidationError('Invalid temperature.')
        if person.temperature!='HOT' and request.data['temperature']=='HOT':start_sla(person,'HOT_CONTACT')
        person.temperature=request.data['temperature']
    person.save();refresh_next_action(person)
    activity(request,person,f'Lifecycle updated: {person.lead_status}',reason,'STATUS')
    audit(request,'LIFECYCLE_CHANGED',person,old,{'stage':person.stage,'status':person.lead_status,'temperature':person.temperature,'student_state':person.student_state,'reason':reason})
    return Response({'ok':True})

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
def reassign(request,pk):
    person=object_person(request,pk,'reassign',True)
    owner=get_object_or_404(User,pk=request.data.get('owner_id'),is_active=True,staff__role='COUNSELOR',staff__branch=person.branch)
    assign_owner(request,person,owner,required_reason(request.data))
    return Response({'ok':True})

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
def archive(request,pk):
    person=object_person(request,pk,'archive',True)
    reason=required_reason(request.data)
    person.archived_at=timezone.now();person.save()
    activity(request,person,'Person archived',reason)
    audit(request,'PERSON_ARCHIVED',person,new={'reason':reason})
    return Response({'ok':True})

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST','PATCH'])
@transaction.atomic
def contacts(request,pk,contact_id=None):
    person=object_person(request,pk,'edit',True)
    if scope(request.user,'edit')=='documents':raise PermissionDenied('Documentation staff cannot edit contact details.')
    require_fields(request.data,['type','raw_value','is_shared','shared_with_note','is_primary','reason'])
    kind=request.data.get('type','PHONE')
    if kind not in ['PHONE','EMAIL','WHATSAPP','MESSENGER']:raise ValidationError('Invalid contact type.')
    raw=str(request.data.get('raw_value','')).strip()
    if not raw or len(raw)>254:raise ValidationError('A contact value is required.')
    if kind in ['PHONE','WHATSAPP']:normalized=normalize_phone(raw)
    elif kind=='EMAIL':normalized=serializers.EmailField().run_validation(raw).lower()
    else:normalized=raw
    shared=request.data.get('is_shared',False)
    if not isinstance(shared,bool):raise ValidationError('Shared flag must be a boolean.')
    if shared and not request.data.get('shared_with_note'):raise ValidationError('Shared contacts require a context note.')
    matches=duplicate_signals(normalized if kind=='PHONE' else '',normalized if kind=='EMAIL' else '',exclude=person.pk)
    if matches:required_reason(request.data)
    item=get_object_or_404(ContactMethod,pk=contact_id,person=person) if contact_id else ContactMethod(person=person)
    old={'raw_value':item.raw_value,'normalized_value':item.normalized_value}
    if item.normalized_value!=normalized:item.verified_at=None;item.verified_via='NONE'
    item.type=kind;item.raw_value=raw;item.normalized_value=normalized;item.is_shared=shared;item.shared_with_note=request.data.get('shared_with_note','');item.is_primary=serializers.BooleanField().run_validation(request.data.get('is_primary',False))
    if item.is_primary:person.contacts.filter(type=kind).update(is_primary=False)
    item.save();queue_candidates(person,matches)
    activity(request,person,'Contact details updated')
    audit(request,'CONTACT_CHANGED',person,old,{'contact_id':item.pk,'reason':request.data.get('reason','')})
    return Response({'id':item.pk},status=200 if contact_id else 201)

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST','PATCH'])
@transaction.atomic
def education(request,pk,kind,item_id=None):
    person=object_person(request,pk,'edit',True)
    model=EducationRecord if kind=='education' else TestScore
    fields=['level','institute','degree_stream','grade_or_percent','passed_year'] if model==EducationRecord else ['test','score','status','test_date']
    require_fields(request.data,fields)
    class ItemSerializer(serializers.ModelSerializer):
        class Meta:
            model = EducationRecord if kind=='education' else TestScore
            fields = ['level','institute','degree_stream','grade_or_percent','passed_year'] if kind=='education' else ['test','score','status','test_date']
    instance=get_object_or_404(model,pk=item_id,person=person) if item_id else None
    serializer=ItemSerializer(instance,data=request.data,partial=bool(instance));serializer.is_valid(raise_exception=True)
    if model==TestScore:
        if serializer.validated_data.get('test',getattr(instance,'test','')) not in ['IELTS','PTE','TOEFL','DUOLINGO','SAT','GRE','GMAT','OTHER']:raise ValidationError('Invalid test type.')
        if serializer.validated_data.get('status',getattr(instance,'status','PLANNED')) not in ['TAKEN','PLANNED','NOT_TAKEN']:raise ValidationError('Invalid test status.')
    item=serializer.save(person=person)
    activity(request,person,f'{kind.replace("_"," ").title()} updated')
    audit(request,'EDUCATION_CHANGED',person,new={'kind':kind,'id':item.pk})
    return Response({'id':item.pk,**serializer.data})

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
def correct_activity(request,pk,item_id):
    person=object_person(request,pk,'work',True)
    original=get_object_or_404(Activity,person=person,pk=item_id)
    subject=serializers.CharField(max_length=200).run_validation(request.data.get('subject'))
    reason=required_reason(request.data)
    item=Activity.objects.create(person=person,type=original.type,channel=original.channel,direction=original.direction,subject=subject,notes=str(request.data.get('notes','')),performed_by=request.user,corrects=original)
    person.last_activity_at=item.performed_at;person.save()
    audit(request,'ACTIVITY_CORRECTED',person,new={'original_id':original.pk,'correction_id':item.pk,'reason':reason})
    return Response({'id':item.pk})

@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
def discovery(request):
    scope(request.user,'discovery')
    search=request.query_params.get('search','').strip()
    if len(search)<3:return Response([])
    own=scoped_people(request.user,Person.objects.filter(archived_at__isnull=True))
    query=Q(full_name__icontains=search)|Q(ref__icontains=search)|Q(contacts__normalized_value__icontains=search)
    try:query|=Q(contacts__normalized_value=normalize_phone(search))
    except ValidationError:pass
    matches=Person.objects.filter(query,archived_at__isnull=True).exclude(pk__in=own).distinct().select_related('owner','branch')[:30]
    return Response([{'id':str(p.pk),'exists':True,'owner_name':p.owner.get_full_name() if p.owner else 'Unassigned','branch_name':p.branch.name,'lead_status':p.lead_status,'created_at':p.created_at} for p in matches])

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
def access_requests(request):
    if request.method=='POST':
        scope(request.user,'request_access')
        person=get_object_or_404(Person,pk=request.data.get('person_id'),archived_at__isnull=True)
        kind=request.data.get('type','ACCESS')
        if kind not in ['ACCESS','REASSIGN']:raise ValidationError('Invalid request type.')
        reason=required_reason(request.data)
        item,created=AccessRequest.objects.get_or_create(person=person,requested_by=request.user,status='PENDING',type=kind,defaults={'reason':reason})
        if created:
            start_sla(person,'ACCESS',item);notify_managers(person.branch,'ACCESS_REQUEST','Access request pending',person)
            audit(request,'ACCESS_REQUESTED',person,new={'request_id':item.pk,'type':kind,'reason':reason})
        return Response({'id':item.pk},status=201)
    if profile(request.user).role in ['ADMIN','MANAGER']:
        items=AccessRequest.objects.filter(person__in=scoped_people(request.user,Person.objects.all(),'reassign'))
    else:items=AccessRequest.objects.filter(requested_by=request.user)
    return Response([{'id':x.pk,'person_id':str(x.person_id),'person_name':x.person.full_name if profile(request.user).role in ['ADMIN','MANAGER'] else 'Existing record','requested_by':x.requested_by.get_full_name(),'type':x.type,'reason':x.reason,'status':x.status,'created_at':x.created_at} for x in items.select_related('person','requested_by').order_by('-created_at')[:200]])

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
def decide_access(request,pk):
    people=scoped_people(request.user,Person.objects.all(),'reassign')
    item=get_object_or_404(AccessRequest.objects.select_for_update(of=('self',)),pk=pk,person__in=people,status='PENDING')
    approved=request.data.get('approved')
    if not isinstance(approved,bool):raise ValidationError('Approved must be a boolean.')
    reason=required_reason(request.data)
    if approved:
        if item.type=='REASSIGN':
            if not item.requested_by.is_active or item.requested_by.staff.branch_id!=item.person.branch_id or item.requested_by.staff.role!='COUNSELOR':raise ValidationError('Reassignment requires an active counselor in this branch.')
            assign_owner(request,item.person,item.requested_by,reason)
        else:AccessGrant.objects.update_or_create(person=item.person,user=item.requested_by,defaults={'active':True})
    item.status='APPROVED' if approved else 'DENIED';item.decided_by=request.user;item.decided_at=timezone.now();item.save()
    item.slatimer_set.filter(satisfied_at__isnull=True).update(satisfied_at=timezone.now())
    notify(item.requested_by,'ACCESS_DECISION',f'Access request {item.status.lower()}',item.person if approved else None)
    audit(request,'ACCESS_DECIDED',item.person,new={'request_id':item.pk,'status':item.status,'reason':reason})
    return Response({'ok':True})

def task_data(t):
    return {'id':t.pk,'person_id':str(t.person_id) if t.person_id else None,'person_name':t.person.full_name if t.person else '', 'owner_id':t.owner_id,'owner_name':t.owner.get_full_name(),'title':t.title,'priority':t.priority,'due_at':t.due_at,'status':t.status,'completed_at':t.completed_at}

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
def tasks(request):
    if request.method=='POST':
        scope(request.user,'work')
        person=object_person(request,request.data['person_id'],'work',True) if request.data.get('person_id') else None
        owner=get_object_or_404(User,pk=request.data.get('owner_id',request.user.pk),is_active=True,staff__branch=profile(request.user).branch)
        if profile(request.user).role not in ['ADMIN','MANAGER'] and owner!=request.user:raise PermissionDenied('Only managers can assign work to another user.')
        priority=request.data.get('priority','NORMAL')
        if priority not in ['LOW','NORMAL','HIGH','URGENT']:raise ValidationError('Invalid priority.')
        title=serializers.CharField(max_length=200).run_validation(request.data.get('title'))
        item=Task.objects.create(person=person,owner=owner,title=title,priority=priority,due_at=safe_date(request.data.get('due_at')))
        if person:refresh_next_action(person);activity(request,person,f'Task created: {title}');audit(request,'TASK_CREATED',person,new={'task_id':item.pk})
        else:
            from types import SimpleNamespace
            audit(request,'TASK_CREATED',SimpleNamespace(id=item.pk,branch=owner.staff.branch),new={'title':title,'owner_id':owner.pk,'due_at':item.due_at.isoformat()})
        notify(owner,'TASK','A task was assigned to you',person)
        return Response(task_data(item),status=201)
    access=scope(request.user,'view')
    items=Task.objects.filter(status__in=['OPEN','IN_PROGRESS'])
    if access=='branch':items=items.filter(owner__staff__branch=profile(request.user).branch)
    elif access in ['own','assigned']:items=items.filter(Q(owner=request.user)|Q(person__in=scoped_people(request.user,Person.objects.all())))
    return Response([task_data(t) for t in items.select_related('owner','person').order_by('due_at')[:500]])

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
def task_update(request,pk):
    scope(request.user,'work')
    item=get_object_or_404(Task.objects.select_for_update(of=('self',)),pk=pk)
    if item.person:object_person(request,item.person_id,'work',True)
    elif item.owner_id!=request.user.pk and not (profile(request.user).role=='ADMIN' or profile(request.user).role=='MANAGER' and item.owner.staff.branch_id==profile(request.user).branch_id):raise PermissionDenied()
    require_fields(request.data,['status','due_at','priority'])
    if 'status' in request.data:
        if request.data['status'] not in ['OPEN','IN_PROGRESS','COMPLETED','CANCELLED']:raise ValidationError('Invalid task status.')
        item.status=request.data['status'];item.completed_at=timezone.now() if item.status=='COMPLETED' else None
    if 'due_at' in request.data:item.due_at=safe_date(request.data['due_at'])
    if 'priority' in request.data:
        if request.data['priority'] not in ['LOW','NORMAL','HIGH','URGENT']:raise ValidationError('Invalid priority.')
        item.priority=request.data['priority']
    item.save()
    if item.person:refresh_next_action(item.person);activity(request,item.person,f'Task {item.status.lower()}: {item.title}');audit(request,'TASK_CHANGED',item.person,new={'task_id':item.pk,'status':item.status})
    else:
        from types import SimpleNamespace
        audit(request,'TASK_CHANGED',SimpleNamespace(id=item.pk,branch=item.owner.staff.branch),new={'status':item.status,'due_at':item.due_at.isoformat()})
    return Response(task_data(item))

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
def followup_update(request,pk):
    item=get_object_or_404(FollowUp.objects.select_for_update(of=('self',)),pk=pk)
    person=object_person(request,item.person_id,'work',True)
    if item.completed_at or item.status=='COMPLETED':raise ValidationError('Completed follow-ups are retained. Schedule a new follow-up for the next conversation.')
    old={'due_at':item.due_at.isoformat(),'status':item.status,'notes':item.notes}
    require_fields(request.data,['due_at','status','notes'])
    if 'due_at' in request.data:item.due_at=safe_date(request.data['due_at'])
    if 'status' in request.data:
        if request.data['status'] not in ['OPEN','IN_PROGRESS','CANCELLED']:raise ValidationError('Use completion with an outcome to complete a follow-up.')
        item.status=request.data['status'];item.completed_at=None
    if 'notes' in request.data:item.notes=str(request.data['notes'])
    item.save();refresh_next_action(person)
    activity(request,person,f'Follow-up updated: {item.subject}',f'Previous schedule: {old["due_at"]} ({old["status"]}). New schedule: {item.due_at.isoformat()} ({item.status}).\nPrevious discussion notes: {old["notes"]}\nNew discussion notes: {item.notes}','FOLLOWUP');audit(request,'FOLLOWUP_CHANGED',person,old=old,new={'followup_id':item.pk,'status':item.status,'due_at':item.due_at.isoformat(),'notes':item.notes})
    return Response({'ok':True})

def materialize_notifications(user):
    people=scoped_people(user,Person.objects.filter(archived_at__isnull=True))
    now=timezone.now()
    for item in FollowUp.objects.filter(person__in=people,completed_at__isnull=True,status__in=['OPEN','IN_PROGRESS'],due_at__lt=now).select_related('person','owner'):
        notify(item.owner,'OVERDUE','Follow-up overdue',item.person,f'followup:{item.pk}:{item.due_at.isoformat()}')
    for timer in SlaTimer.objects.filter(person__in=people,satisfied_at__isnull=True,due_at__lt=now).select_related('person','branch','rule'):
        if not timer.breached_at:SlaTimer.objects.filter(pk=timer.pk,breached_at__isnull=True).update(breached_at=now)
        notify(timer.person.owner,'SLA_BREACH',f'SLA breached: {timer.rule.name}',timer.person,f'sla:{timer.pk}:{timer.due_at.isoformat()}')
        notify_managers(timer.branch,'SLA_BREACH',f'SLA breached: {timer.rule.name}',timer.person,f'sla:{timer.pk}:{timer.due_at.isoformat()}')

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
def notifications(request):
    if request.method=='POST':
        ids=request.data.get('ids',[])
        if not isinstance(ids,list) or len(ids)>500:raise ValidationError('Use a list of up to 500 notification IDs.')
        Notification.objects.filter(recipient=request.user,pk__in=ids).update(read_at=timezone.now())
    if 'view' in MATRIX.get(profile(request.user).role,{}):materialize_notifications(request.user)
    items=Notification.objects.filter(recipient=request.user).order_by('-created_at')[:100]
    return Response([{'id':n.pk,'type':n.type,'title':n.title,'message':n.message,'person_id':str(n.person_id) if n.person_id else None,'read_at':n.read_at,'created_at':n.created_at} for n in items])

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
def duplicates_review(request,pk=None):
    people=scoped_people(request.user,Person.objects.all(),'review')
    items=DuplicateCandidate.objects.filter(person_a__in=people,person_b__in=people).select_related('person_a','person_b')
    if request.method=='POST':
        item=get_object_or_404(items.select_for_update(of=('self',)),pk=pk,status='OPEN')
        item.status='DISMISSED';item.reviewed_by=request.user;item.save()
        audit(request,'DUPLICATE_DISMISSED',item.person_a,new={'candidate_id':item.pk,'reason':required_reason(request.data)})
        return Response({'ok':True})
    from .serializers import PersonSerializer
    return Response([{'id':d.pk,'confidence':d.confidence,'signals':d.signals,'status':d.status,'person_a':PersonSerializer(d.person_a).data,'person_b':PersonSerializer(d.person_b).data} for d in items.filter(status='OPEN')[:200]])

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
@idempotent(required=True)
def merge_people(request):
    scope(request.user,'merge')
    if request.method=='GET':
        items=MergeRecord.objects.filter(survivor__in=scoped_people(request.user,Person.objects.all(),'merge')).select_related('survivor','merged','performed_by')
        return Response([{'id':r.pk,'survivor_name':r.survivor.full_name,'merged_name':r.merged.full_name,'survivor_id':str(r.survivor_id),'performed_by':r.performed_by.get_full_name(),'reversible_until':r.reversible_until,'reversed_at':r.reversed_at} for r in items.order_by('-pk')[:100]])
    survivor=object_person(request,request.data.get('survivor_id'),'merge',True)
    merged=object_person(request,request.data.get('merged_id'),'merge',True)
    if survivor.pk==merged.pk:raise ValidationError('Select two different people.')
    if survivor.branch_id!=merged.branch_id:raise ValidationError('Move records into the same branch before merging.')
    reason=required_reason(request.data)
    if survivor.stage=='LEAD' and merged.stage=='STUDENT':raise ValidationError('Choose the student record as survivor to preserve lifecycle history.')
    choices=request.data.get('field_choices',{})
    if not isinstance(choices,dict):raise ValidationError('Field choices must be an object.')
    allowed=set(EditPersonSerializer.Meta.fields)-{'tags','source','campaign','source_detail'}
    old_values={}
    for field,origin in choices.items():
        if field not in allowed or origin not in ['survivor','merged']:raise ValidationError('Invalid field choice.')
        if origin=='merged':
            from datetime import date,datetime
            from copy import deepcopy
            value=getattr(survivor,field)
            old_values[field]=value.isoformat() if isinstance(value,(date,datetime)) else deepcopy(value)
            setattr(survivor,field,getattr(merged,field))
    survivor.save()
    moved={}
    models=[ContactMethod,ConsentRecord,Activity,FollowUp,Task,EducationRecord,TestScore,OwnershipHistory,Notification,SlaTimer,Document]
    from intake.models import IntakeSubmission
    models.append(IntakeSubmission)
    from admissions.models import Application,Document as AdmissionDocument,Deadline,Blocker,StudentPreference
    if StudentPreference.objects.filter(person=merged).exists() and StudentPreference.objects.filter(person=survivor).exists():
        raise ValidationError('Both records have admissions preferences. Reconcile these before merging.')
    models.extend([Application,AdmissionDocument,Deadline,Blocker,StudentPreference])
    from progression.models import Payment
    from finance.models import Invoice
    from student_experience.models import StudentRequest,PrivateLink
    PrivateLink.objects.filter(person__in=[merged,survivor],active=True).update(active=False)
    models.extend([Payment,Invoice,StudentRequest])
    for model in models:
        ids=list(model.objects.filter(person=merged).values_list('pk',flat=True))
        moved[model._meta.label]=[str(x) for x in ids]
        model.objects.filter(pk__in=ids).update(person=survivor)
    moved['tags_before']=list(survivor.tags.values_list('pk',flat=True))
    survivor.tags.add(*merged.tags.all())
    moved['tags_after']=list(survivor.tags.values_list('pk',flat=True))
    moved['relationship_changes']=[]
    for model in [TeamMember,AccessGrant]:
        ids=[]
        for item in model.objects.filter(person=merged):
            existing=model.objects.filter(person=survivor,user=item.user).first()
            if existing:
                if item.active and not existing.active:
                    moved['relationship_changes'].append({'model':model._meta.label,'id':existing.pk,'old_active':False})
                    existing.active=True;existing.save()
            else:item.person=survivor;item.save(update_fields=['person']);ids.append(str(item.pk))
        moved[model._meta.label]=ids
    history_access=list(AccessRequest.objects.filter(person=merged).values_list('pk',flat=True))
    moved['crm.AccessRequest']=[str(x) for x in history_access]
    AccessRequest.objects.filter(pk__in=history_access).update(person=survivor)
    record=MergeRecord.objects.create(survivor=survivor,merged=merged,performed_by=request.user,field_choices={'choices':choices,'old_values':old_values,'reason':reason},moved_objects=moved,reversible_until=timezone.now()+timedelta(days=7))
    merged.archived_at=timezone.now();merged.merged_into=survivor;merged.save()
    DuplicateCandidate.objects.filter(Q(person_a=merged)|Q(person_b=merged)).update(status='MERGED',reviewed_by=request.user)
    refresh_next_action(survivor);refresh_next_action(merged)
    activity(request,survivor,f'Merged record {merged.ref}',reason,'MERGE')
    audit(request,'PERSON_MERGED',survivor,new={'merge_id':record.pk,'merged_id':str(merged.pk),'moved_counts':{key:len(value) for key,value in moved.items()},'reason':reason})
    return Response({'merge_id':record.pk,'survivor_id':str(survivor.pk)})

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
def reverse_merge(request,pk):
    if profile(request.user).role!='ADMIN':raise PermissionDenied('Only Super Admin can reverse a merge.')
    record=get_object_or_404(MergeRecord.objects.select_for_update(of=('self',)),pk=pk,reversed_at__isnull=True)
    if record.reversible_until<timezone.now():raise ValidationError('The seven-day reversal window has expired.')
    reason=required_reason(request.data)
    from django.apps import apps
    if 'tags_after' in record.moved_objects and set(record.survivor.tags.values_list('pk',flat=True))!=set(record.moved_objects['tags_after']):raise ValidationError('Tags changed after merge; manual reconciliation is required.')
    changes=AuditEvent.objects.filter(object_id=str(record.survivor_id),at__gt=record.performed_at,action__in=['OWNER_CHANGED','TEAM_CHANGED','ACCESS_DECIDED','CONTACT_CHANGED','EDUCATION_CHANGED','TASK_CHANGED','FOLLOWUP_CHANGED'])
    for change in changes:
        if change.action in ['OWNER_CHANGED','TEAM_CHANGED','ACCESS_DECIDED']:raise ValidationError('Assignment or permissions changed after merge; manual reconciliation is required.')
        key,models={'CONTACT_CHANGED':('contact_id',['crm.ContactMethod']),'EDUCATION_CHANGED':('id',['crm.EducationRecord','crm.TestScore']),'TASK_CHANGED':('task_id',['crm.Task']),'FOLLOWUP_CHANGED':('followup_id',['crm.FollowUp'])}[change.action]
        if any(str(change.new.get(key)) in record.moved_objects.get(model,[]) for model in models):raise ValidationError('A moved record changed after merge; manual reconciliation is required.')
    from admissions.models import ApplicationEvent
    moved_apps=record.moved_objects.get('admissions.Application',[])
    if ApplicationEvent.objects.filter(application_id__in=moved_apps,created_at__gt=record.performed_at).exists():raise ValidationError('Moved applications changed after merge; manual reconciliation is required.')
    from finance.models import FinanceEvent,PaymentAllocation,InstitutionCommission
    moved_payments=record.moved_objects.get('progression.Payment',[]);moved_invoices=record.moved_objects.get('finance.Invoice',[])
    moved_commissions=InstitutionCommission.objects.filter(application_id__in=moved_apps).values_list('pk',flat=True)
    changed_finance=Q(object_kind='payments',object_id__in=moved_payments)|Q(object_kind='invoices',object_id__in=moved_invoices)|Q(object_kind='commissions',object_id__in=[str(pk) for pk in moved_commissions])
    if FinanceEvent.objects.filter(changed_finance,created_at__gt=record.performed_at).exists() or PaymentAllocation.objects.filter(Q(payment_id__in=moved_payments)|Q(invoice_id__in=moved_invoices),created_at__gt=record.performed_at).exists():raise ValidationError('Moved financial records changed after merge; manual reconciliation is required.')
    for model_name,ids in record.moved_objects.items():
        if model_name in ['tags_before','tags_after','relationship_changes']:continue
        model=apps.get_model(model_name)
        # Later edits or moves require manual reconciliation rather than overwriting.
        if model.objects.filter(pk__in=ids).exclude(person=record.survivor).exists():raise ValidationError('Some moved records changed ownership; manual reconciliation is required.')
        if model in [TeamMember,AccessGrant]:
            for item in model.objects.filter(pk__in=ids):
                if model.objects.filter(person=record.merged,user=item.user).exists():raise ValidationError('Team/grant relationships require manual reconciliation.')
        model.objects.filter(pk__in=ids).update(person=record.merged)
    # Do not overwrite subsequent survivor field edits.
    for field,old_value in record.field_choices.get('old_values',{}).items():
        if str(getattr(record.survivor,field))!=str(getattr(record.merged,field)):raise ValidationError('Survivor fields changed after merge; manual reconciliation is required.')
        setattr(record.survivor,field,record.survivor._meta.get_field(field).to_python(old_value))
    for change in record.moved_objects.get('relationship_changes',[]):
        model=apps.get_model(change['model']);model.objects.filter(pk=change['id']).update(active=change['old_active'])
    record.survivor.save()
    record.survivor.tags.set(record.moved_objects.get('tags_before',[]))
    record.merged.archived_at=None;record.merged.merged_into=None;record.merged.save()
    record.reversed_at=timezone.now();record.save()
    refresh_next_action(record.survivor);refresh_next_action(record.merged)
    activity(request,record.survivor,'Merge reversed',reason,'MERGE')
    audit(request,'MERGE_REVERSED',record.survivor,new={'merge_id':record.pk,'reason':reason})
    return Response({'ok':True})

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
@idempotent(required=True)
def bulk(request):
    ids=request.data.get('ids',[])
    if not isinstance(ids,list) or not ids or len(ids)>500:raise ValidationError('Select 1–500 person IDs.')
    people=list(scoped_people(request.user,Person.objects.filter(archived_at__isnull=True),'bulk').filter(pk__in=ids).select_for_update(of=('self',)))
    if len(people)!=len(set(ids)):raise PermissionDenied('Every selected record must be in your scope.')
    operation=request.data.get('operation')
    reason=required_reason(request.data)
    for person in people:
        if operation=='reassign':
            owner=get_object_or_404(User,pk=request.data.get('owner_id'),is_active=True,staff__branch=person.branch,staff__role='COUNSELOR')
            assign_owner(request,person,owner,reason)
        elif operation=='tag':
            tag=get_object_or_404(Tag,pk=request.data.get('tag_id'),active=True)
            person.tags.add(tag);person.save();activity(request,person,f'Tag added: {tag.name}',reason)
        elif operation=='status':
            # Use the same lifecycle service and validation for every record.
            from types import SimpleNamespace
            proxy=SimpleNamespace(user=request.user,request_id=request.request_id,data={'status':request.data.get('status'),'reason':reason,'lost_reason_id':request.data.get('lost_reason_id'),'hold_until':request.data.get('hold_until')})
            lifecycle.cls().post(proxy,person.pk)
        else:raise ValidationError('Operation must be reassign, tag, or status.')
        audit(request,'BULK_UPDATED',person,new={'operation':operation,'count':len(people),'reason':reason})
    return Response({'count':len(people)})

def csv_safe(value):
    value=str(value or '')
    return "'"+value if value.startswith(('=','+','-','@','\t','\r')) else value

@extend_schema(responses=OpenApiTypes.BINARY)
@api_view(['GET'])
def export_people(request):
    access=scope(request.user,'export')
    if access=='reports':
        people=scoped_people(request.user,Person.objects.filter(archived_at__isnull=True))
        rows=[['Status','Count']]+[[s,people.filter(lead_status=s).count()] for s in Person.Status.values]
        count=people.count()
    else:
        people=scoped_people(request.user,Person.objects.filter(archived_at__isnull=True),'export').select_related('owner','branch','source').prefetch_related('contacts')
        from .api import filtered_people
        people=filtered_people(people,request.query_params)
        rows=[['Reference','Name','Phone','Email','Branch','Owner','Source','Country','Status','Temperature']]
        for p in people:
            contacts=list(p.contacts.all())
            rows.append([p.ref,p.full_name,next((c.normalized_value for c in contacts if c.type=='PHONE'),''),next((c.normalized_value for c in contacts if c.type=='EMAIL'),''),p.branch.name,p.owner.get_full_name() if p.owner else '',p.source.name,p.preferred_country,p.lead_status,p.temperature])
        count=people.count()
    output=io.StringIO();writer=csv.writer(output)
    for row in rows:writer.writerow([csv_safe(value) for value in row])
    AuditEvent.objects.create(actor=request.user,branch=profile(request.user).branch,action='PEOPLE_EXPORTED',object_id='export',request_id=request.request_id,new={'filters':dict(request.query_params),'count':count,'sensitive_fields':False})
    response=HttpResponse('\ufeff'+output.getvalue(),content_type='text/csv; charset=utf-8')
    response['Content-Disposition']='attachment; filename="consman-people.csv"'
    return response

@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
def audit_log(request):
    items=branch_scope(request.user,AuditEvent.objects.all(),'audit')
    action=request.query_params.get('action')
    if action:items=items.filter(action__icontains=action)
    return Response([{'id':a.pk,'actor':a.actor.get_full_name() if a.actor else 'System','action':a.action,'object_id':a.object_id,'old':a.old,'new':a.new,'at':a.at,'request_id':a.request_id} for a in items.select_related('actor').order_by('-at')[:300]])
