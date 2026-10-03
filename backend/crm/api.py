from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.db import transaction, connection, DatabaseError
from django.db.models import Q
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
import time
from datetime import timedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView, exception_handler as default_exception_handler
from rest_framework.viewsets import ViewSet
from rest_framework.decorators import action
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from django.shortcuts import get_object_or_404
from .models import Branch, ConsentRecord, ContactMethod, FollowUp, Person, Source, StaffProfile, MfaDevice, Campaign, SourceDetail, Tag, LostReason, Task, SlaTimer, SystemPolicy
from .permissions import profile, scope, scoped_people, MATRIX
from .serializers import ActivitySerializer, CreatePersonSerializer, FollowUpSerializer, PersonSerializer, LoginSerializer, OutcomeSerializer, SessionSerializer, LoginResultSerializer, PersonListSerializer, PersonDetailSerializer, MastersSerializer, DashboardSerializer
from .services import activity, audit, duplicates, next_reference, normalize_phone, refresh_next_action, duplicate_signals, queue_candidates, notify, assign_owner
from .idempotency import idempotent
from .calendar import start_sla

def exception_handler(exc, context):
    response = default_exception_handler(exc, context)
    if response is not None:
        response.data = {'code': getattr(exc, 'default_code', 'validation_error'), 'message': str(response.data.get('detail', 'Please check the submitted information.')) if isinstance(response.data, dict) else 'Request failed.', 'fields': response.data}
    return response

def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except DatabaseError:
        return JsonResponse({'status': 'unavailable', 'service': 'consman'}, status=503)
    return JsonResponse({'status': 'ok', 'service': 'consman'})

def csrf_failure(request, reason=''):
    return JsonResponse({'code': 'csrf_failed', 'message': 'Refresh the page and try again.', 'fields': {}}, status=403)

@extend_schema(responses=SessionSerializer)
@api_view(['GET'])
@permission_classes([AllowAny])
def session(request):
    token = get_token(request)
    if not request.user.is_authenticated:
        return Response({'user': None, 'csrf_token': token})
    staff = profile(request.user)
    return Response({'user': {'id': request.user.id, 'name': request.user.get_full_name(), 'role': staff.role, 'branch': staff.branch.name,'branch_id':staff.branch_id,'permissions':MATRIX.get(staff.role,{})}, 'csrf_token': token})

class LoginThrottle(AnonRateThrottle):
    scope = 'login'

@method_decorator(csrf_protect, name='dispatch')
class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]
    @extend_schema(request=LoginSerializer, responses=LoginResultSerializer)
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request, **serializer.validated_data)
        if not user or not hasattr(user, 'staff'):
            raise ValidationError({'credentials': 'Username or password is incorrect.'})
        logout(request)
        request.session['password_verified_at']=time.time()
        device=MfaDevice.objects.filter(user=user,confirmed=True).first()
        if user.staff.role in ['ADMIN','FINANCE'] or device:
            request.session.cycle_key()
            request.session['mfa_pending']=user.pk
            request.session['mfa_until']=time.time()+300
            request.session['mfa_attempts']=0
            return Response({'mfa_required':True,'enroll':not bool(device),'csrf_token':get_token(request)})
        login(request, user)
        return Response({'ok': True, 'csrf_token': get_token(request)})

@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
def logout_view(request):
    logout(request)
    return Response({'ok': True})

@extend_schema(responses=MastersSerializer)
@api_view(['GET'])
def masters(request):
    staff = profile(request.user)
    if staff.role=='FINANCE':return Response({'branches':[],'sources':[],'owners':[],'campaigns':[],'source_details':[],'tags':[],'lost_reasons':[],'users':[]})
    scope(request.user, 'view')
    branches = Branch.objects.all() if staff.role in ['ADMIN','MANAGEMENT'] else Branch.objects.filter(pk=staff.branch_id)
    owners = StaffProfile.objects.filter(role='COUNSELOR', user__is_active=True, branch__in=branches).select_related('user')
    if staff.role == 'COUNSELOR':
        owners = owners.filter(user=request.user)
    return Response({'branches': list(branches.values('id','name')), 'sources': list(Source.objects.filter(active=True).values('id','name')), 'owners': [{'id': o.user_id, 'name': o.user.get_full_name(), 'branch_id': o.branch_id} for o in owners], 'campaigns':list(Campaign.objects.filter(active=True).values('id','name','source_id')),'source_details':list(SourceDetail.objects.filter(active=True).values('id','name','campaign_id')),'tags':list(Tag.objects.filter(active=True).values('id','name')),'lost_reasons':list(LostReason.objects.filter(active=True).values('id','name','note_required')),'users':[{'id':p.user_id,'name':p.user.get_full_name(),'role':p.role,'branch_id':p.branch_id} for p in StaffProfile.objects.filter(user__is_active=True,branch__in=branches).select_related('user')]})

def filtered_people(people,params):
    query=params.get('search','').strip()
    if query:
        contacts=Q(normalized_value__icontains=query)
        try:contacts|=Q(normalized_value=normalize_phone(query))
        except ValidationError:pass
        search=Q(full_name__icontains=query)|Q(ref__icontains=query)|Q(pk__in=ContactMethod.objects.filter(contacts).values('person_id'))
        people=people.filter(search)
    for field in ['lead_status','temperature','preferred_country','stage','owner_id','branch_id','source_id','campaign_id']:
        if params.get(field):
            if field.endswith('_id'):
                try:int(params[field])
                except (ValueError,TypeError):raise ValidationError({field:'Enter a numeric identifier.'})
            people=people.filter(**{field:params[field]})
    if params.get('tag_id'):people=people.filter(tags__id=params['tag_id'])
    for field,lookup in [('created_from','created_at__date__gte'),('created_to','created_at__date__lte')]:
        if params.get(field):
            from datetime import date
            try:date.fromisoformat(params[field])
            except ValueError:raise ValidationError({field:'Use YYYY-MM-DD.'})
            people=people.filter(**{lookup:params[field]})
    attention=params.get('attention')
    if attention=='overdue':people=people.filter(next_action_due_at__lt=timezone.now())
    if attention=='today':people=people.filter(next_action_due_at__date=timezone.localdate())
    if attention=='no_action':people=people.filter(next_action_due_at__isnull=True,stage='LEAD').exclude(lead_status__in=['LOST','ON_HOLD'])
    if attention=='unassigned':people=people.filter(owner__isnull=True)
    if attention=='stale':
        days=SystemPolicy.objects.filter(key='stale_days').values_list('value',flat=True).first() or 30
        cutoff=timezone.now()-timedelta(days=int(days))
        people=people.filter(Q(last_activity_at__lt=cutoff)|Q(last_activity_at__isnull=True,created_at__lt=cutoff))
    return people

@extend_schema_view(
    list=extend_schema(responses=PersonListSerializer, parameters=[OpenApiParameter(name=p, type=str) for p in ['search','lead_status','temperature','preferred_country','stage','attention','page']]),
    create=extend_schema(request=CreatePersonSerializer, responses={201:PersonSerializer,409:OpenApiTypes.OBJECT}),
    retrieve=extend_schema(responses=PersonDetailSerializer),
)
class PersonViewSet(ViewSet):
    serializer_class = PersonSerializer
    queryset = Person.objects.none()
    def people(self, request, action='view'):
        return scoped_people(request.user, Person.objects.filter(archived_at__isnull=True).select_related('branch','owner','source','lost_reason').prefetch_related('contacts','tags'), action)
    def list(self, request):
        people = filtered_people(self.people(request),request.query_params)
        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except ValueError:
            raise ValidationError({'page': 'Enter a valid page number.'})
        return Response({'count': people.count(), 'results': PersonSerializer(people[(page-1)*50:page*50], many=True).data})
    @idempotent()
    @transaction.atomic
    def create(self, request):
        access = scope(request.user, 'create')
        if profile(request.user).role=='FRONTDESK':raise PermissionDenied('Use paper registration and verify the visitor OTP before creating a lead.')
        serializer = CreatePersonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        phone = normalize_phone(values.pop('phone'))
        email = values.pop('email', '').strip().lower()
        values.pop('consent')
        reason = values.pop('duplicate_reason', '')
        branch = get_object_or_404(Branch, pk=values['branch_id'])
        owner = get_object_or_404(User, pk=values['owner_id'], is_active=True, staff__role='COUNSELOR', staff__branch=branch)
        get_object_or_404(Source, pk=values['source_id'])
        if access != 'full' and branch.pk != profile(request.user).branch_id:
            raise PermissionDenied('You can create records only in your branch.')
        if access == 'own' and owner != request.user:
            raise PermissionDenied('Counselors can create only their own records.')
        signals=duplicate_signals(phone,email,values['full_name'],values.get('dob'),country=values.get('preferred_country',''))
        matches=Person.objects.filter(pk__in=[m['person'].pk for m in signals if m['confidence']!='LOW'])
        if matches.exists():
            visible = set(self.people(request).values_list('id', flat=True))
            summaries = [{'id': str(p.id), 'owner': p.owner.get_full_name() if p.owner else 'Unassigned', 'branch': p.branch.name, 'status': p.lead_status, 'created_at': p.created_at, **({'name': p.full_name, 'ref': p.ref} if p.id in visible else {'masked': True})} for p in matches.select_related('owner','branch')[:10]]
            # Conflicts with another owner's record require the future access-request flow.
            if access == 'own' and matches.exclude(owner=request.user).exists():
                return Response({'code': 'duplicate_access_required', 'message': 'An existing record requires a manager access review.', 'matches': summaries}, status=409)
            if not reason:
                return Response({'code': 'duplicate_review', 'message': 'Matching contacts found. Review before continuing; a reason is required.', 'matches': summaries}, status=409)
        person = Person.objects.create(**values, ref=next_reference(), created_by=request.user)
        ContactMethod.objects.create(person=person, type='PHONE', raw_value=request.data['phone'], normalized_value=phone)
        if email:
            ContactMethod.objects.create(person=person, type='EMAIL', raw_value=request.data['email'], normalized_value=email)
        ConsentRecord.objects.create(person=person, recorded_by=request.user)
        queue_candidates(person,signals)
        start_sla(person,'FIRST_CONTACT')
        if person.temperature=='HOT':start_sla(person,'HOT_CONTACT')
        from .models import OwnershipHistory
        OwnershipHistory.objects.create(person=person,from_user=None,to_user=owner,reason='Manual lead creation',changed_by=request.user,request_id=request.request_id)
        notify(owner,'ASSIGNMENT','New lead assigned to you',person)
        activity(request, person, 'Lead created', kind='CREATED')
        audit(request, 'PERSON_CREATED', person, new={'ref': person.ref, 'owner_id': owner.id, 'duplicate_override_reason': reason})
        return Response(PersonSerializer(person).data, status=201)
    def retrieve(self, request, pk=None):
        person = self.people(request).filter(pk=pk).first()
        if not person and 'discovery' in MATRIX.get(profile(request.user).role,{}):
            other=get_object_or_404(Person.objects.select_related('owner','branch'),pk=pk,archived_at__isnull=True)
            return Response({'masked':True,'record':{'id':str(other.pk),'exists':True,'owner_name':other.owner.get_full_name() if other.owner else 'Unassigned','branch_name':other.branch.name,'lead_status':other.lead_status,'created_at':other.created_at}})
        if not person:person=get_object_or_404(self.people(request),pk=pk)
        from .operations import task_data
        activities=list(person.activities.select_related('performed_by','corrects').prefetch_related('corrections'))
        timeline=[{'id':f'activity-{a.pk}','kind':a.type,'subject':a.subject,'notes':a.notes,'actor':a.performed_by.get_full_name() if a.performed_by else 'System','at':a.performed_at,'edited':bool(a.corrects_id or a.corrections.all()),'corrects_id':a.corrects_id} for a in activities]
        now=timezone.now()
        for f in person.followups.filter(completed_at__isnull=True,status__in=['OPEN','IN_PROGRESS'],due_at__lt=now):timeline.append({'id':f'overdue-followup-{f.pk}','kind':'OVERDUE','subject':f'Follow-up overdue: {f.subject}','notes':'','actor':'System','at':f.due_at,'edited':False})
        for t in person.tasks.filter(status__in=['OPEN','IN_PROGRESS'],due_at__lt=now):timeline.append({'id':f'overdue-task-{t.pk}','kind':'OVERDUE','subject':f'Task overdue: {t.title}','notes':'','actor':'System','at':t.due_at,'edited':False})
        timeline.sort(key=lambda e:e['at'],reverse=True)
        return Response({'person': PersonSerializer(person).data, 'activities': ActivitySerializer(activities, many=True).data, 'followups': FollowUpSerializer(person.followups.all(), many=True).data,'timeline':timeline,'contacts':list(person.contacts.values()),'education':list(person.education.values()),'test_scores':list(person.test_scores.values()),'tasks':[task_data(t) for t in person.tasks.select_related('owner','person')],'sla_timers':list(person.sla_timers.values('id','rule__name','due_at','satisfied_at','breached_at')),'team_members':list(person.team_members.values('id','user_id','role_on_case','active'))})
    @extend_schema(request=ActivitySerializer, responses={201:ActivitySerializer})
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def activities(self, request, pk=None):
        person = get_object_or_404(self.people(request, 'work').select_for_update(of=('self',)), pk=pk)
        serializer = ActivitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        item = activity(request, person, values['subject'], values.get('notes', ''), values['type'])
        for field in ['channel','direction','outcome']:
            if field in values:setattr(item,field,values[field])
        item.save(update_fields=['channel','direction','outcome'])
        audit(request, 'ACTIVITY_CREATED', person, new={'activity_id': item.pk})
        return Response(ActivitySerializer(item).data, status=201)
    @extend_schema(request=FollowUpSerializer, responses={201:FollowUpSerializer})
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def followups(self, request, pk=None):
        person = get_object_or_404(self.people(request, 'work').select_for_update(of=('self',)), pk=pk)
        serializer = FollowUpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = serializer.save(person=person, owner=request.user)
        refresh_next_action(person)
        activity(request, person, f'Follow-up scheduled: {item.subject}')
        audit(request, 'FOLLOWUP_CREATED', person, new={'followup_id': item.pk, 'due_at': item.due_at.isoformat()})
        return Response(FollowUpSerializer(item).data, status=201)
    @extend_schema(request=None, responses=PersonSerializer)
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def convert(self, request, pk=None):
        person = get_object_or_404(self.people(request, 'convert').select_for_update(of=('self',)), pk=pk)
        if person.stage != 'LEAD' or person.lead_status in ['LOST','ON_HOLD']:
            raise ValidationError({'stage': 'Only active leads can be converted.'})
        old = {'stage': person.stage, 'lead_status': person.lead_status}
        person.stage, person.lead_status, person.student_state = 'STUDENT', 'CONVERTED', 'ACTIVE'
        person.converted_at = timezone.now()
        person.save()
        activity(request, person, 'Converted to student', kind='CONVERSION')
        audit(request, 'PERSON_CONVERTED', person, old, {'stage': 'STUDENT', 'lead_status': 'CONVERTED'})
        return Response(PersonSerializer(person).data)

@extend_schema(responses=FollowUpSerializer(many=True))
@api_view(['GET'])
def followup_queue(request):
    people = scoped_people(request.user, Person.objects.filter(archived_at__isnull=True))
    items = FollowUp.objects.filter(person__in=people).select_related('person','owner','completed_by')
    if request.query_params.get('include_completed')=='1':
        from django.db.models.functions import Coalesce
        items=items.annotate(recorded_at=Coalesce('completed_at','due_at')).order_by('-recorded_at','-pk')
    else:items=items.filter(completed_at__isnull=True,status__in=['OPEN','IN_PROGRESS'])
    return Response(FollowUpSerializer(items, many=True).data)

@extend_schema(request=OutcomeSerializer, responses=FollowUpSerializer)
@api_view(['POST'])
@transaction.atomic
def complete_followup(request, pk):
    people = scoped_people(request.user, Person.objects.filter(archived_at__isnull=True), 'work')
    item = get_object_or_404(FollowUp.objects.select_for_update(of=('self',)), pk=pk, person__in=people)
    outcome = str(request.data.get('outcome', '')).strip()
    if not outcome or len(outcome) > 200:
        raise ValidationError({'outcome': 'A completion outcome of 1–200 characters is required.'})
    if item.completed_at:
        raise ValidationError({'followup': 'This follow-up is already completed.'})
    # Serialize next-action updates with concurrent scheduling for this person.
    person = Person.objects.select_for_update(of=('self',)).get(pk=item.person_id)
    item.completed_at, item.outcome = timezone.now(), outcome
    item.status='COMPLETED';item.completed_by=request.user
    item.save()
    refresh_next_action(person)
    activity(request, item.person, f'Follow-up completed: {item.subject}', outcome, 'FOLLOWUP')
    audit(request, 'FOLLOWUP_COMPLETED', item.person, new={'followup_id': item.pk, 'outcome': outcome})
    return Response(FollowUpSerializer(item).data)

@extend_schema(responses=DashboardSerializer)
@api_view(['GET'])
def dashboard(request):
    if profile(request.user).role=='FINANCE':return Response({'people':0,'active_leads':0,'hot_leads':0,'overdue':0,'due_today':0,'no_action':0,'students':0,'pipeline':[],'recent':[]})
    people = scoped_people(request.user, Person.objects.filter(archived_at__isnull=True))
    leads = people.filter(stage='LEAD').exclude(lead_status__in=['LOST','ON_HOLD'])
    now = timezone.now()
    today = timezone.localdate()
    followups = FollowUp.objects.filter(person__in=people, completed_at__isnull=True,status__in=['OPEN','IN_PROGRESS'])
    from intake.models import IntakeSubmission
    from .permissions import branch_scope
    intake=branch_scope(request.user,IntakeSubmission.objects.all(),'intake',field='qr__branch') if 'intake' in MATRIX.get(profile(request.user).role,{}) else IntakeSubmission.objects.none()
    intake_count=intake.count();verified_count=intake.filter(verified_at__isnull=False).count()
    metrics={'unassigned':leads.filter(owner__isnull=True).count(),'unassigned_breached':SlaTimer.objects.filter(person__in=leads,person__owner__isnull=True,rule__trigger='ASSIGNMENT',satisfied_at__isnull=True,due_at__lt=now).count(),'sla_breaches':SlaTimer.objects.filter(person__in=people,satisfied_at__isnull=True,due_at__lt=now).count(),'unverified_intake':intake.filter(status='UNVERIFIED').count(),'verification_rate':round(verified_count/max(1,intake_count)*100,1)}
    return Response({**metrics,'people': people.count(), 'active_leads': leads.count(), 'hot_leads': leads.filter(temperature='HOT').count(), 'overdue': followups.filter(due_at__lt=now).count(), 'due_today': followups.filter(due_at__date=today).count(), 'no_action': leads.filter(next_action_due_at__isnull=True).count(), 'students': people.filter(stage='STUDENT').count(), 'pipeline': [{'status': s, 'count': people.filter(stage='LEAD', lead_status=s).count()} for s in ['NEW','CONTACTED','COUNSELING','INTERESTED','DOCUMENT_COLLECTION','APPLICATION_READY']], 'recent': PersonSerializer(people.select_related('owner','branch','source','lost_reason').prefetch_related('contacts','tags')[:5], many=True).data})
