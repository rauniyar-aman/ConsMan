from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from crm.models import Person, FollowUp
from crm.permissions import scoped_people, profile
from crm.services import audit
from crm.operations import required_reason
from admissions.models import Application, Deadline, Document
from .models import AssistanceReview, AssistanceGeneration
from .services import evidence, priority
from . import provider


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(['GET', 'POST'])
@transaction.atomic
def person(request, pk):
    record = get_object_or_404(scoped_people(request.user, Person.objects.filter(archived_at__isnull=True, merged_into__isnull=True), 'edit' if request.method == 'POST' else 'view'), pk=pk)
    if request.method == 'POST':
        if profile(request.user).role not in ['ADMIN', 'MANAGER', 'COUNSELOR']:
            raise PermissionDenied('Only responsible staff can review assistance.')
        kind = serializers.ChoiceField(['SUMMARY', 'MATCHING', 'DOCUMENTS', 'PRIORITY', 'TIMELINE', 'DRAFT']).run_validation(request.data.get('kind'))
        decision = serializers.ChoiceField(['ACCEPTED', 'DISMISSED']).run_validation(request.data.get('decision'))
        snapshot = evidence(record)
        if request.data.get('generation_id'):
            generation = get_object_or_404(AssistanceGeneration.objects.filter(Q(person=record)|Q(person__merged_into=record)), pk=serializers.UUIDField().run_validation(request.data['generation_id']))
            snapshot['provider_generation'] = {'id': str(generation.pk), 'result': generation.result}
        review = AssistanceReview.objects.create(person=record, reviewer=request.user, kind=kind,
            decision=decision, evidence=snapshot, note=required_reason(request.data))
        audit(request, 'ASSISTANCE_REVIEWED', record, new={'review_id': str(review.pk), 'kind': kind, 'decision': decision})
        return Response({'id': str(review.pk), 'notice': 'Review recorded. Case records and messages remain unchanged.'}, status=201)
    data = evidence(record)
    data['text_assistance_configured'] = provider.configuration()
    data['generations'] = [{'id': str(g.pk), 'result': g.result, 'at': g.created_at} for g in AssistanceGeneration.objects.filter(Q(person=record)|Q(person__merged_into=record)).order_by('-created_at')[:5]]
    data['reviews'] = [{'id': str(r.pk), 'kind': r.kind, 'decision': r.decision, 'note': r.note,
        'reviewer': r.reviewer.get_full_name() or r.reviewer.username, 'at': r.created_at} for r in AssistanceReview.objects.filter(Q(person=record)|Q(person__merged_into=record)).select_related('reviewer').order_by('-created_at')[:30]]
    return Response(data)


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
def generate(request, pk):
    if profile(request.user).role not in ['ADMIN', 'MANAGER', 'COUNSELOR']:
        raise PermissionDenied('Only responsible staff can request text assistance.')
    record = get_object_or_404(scoped_people(request.user, Person.objects.filter(archived_at__isnull=True, merged_into__isnull=True), 'edit'), pk=pk)
    reason = required_reason(request.data)
    result = provider.generate(evidence(record))
    item = AssistanceGeneration.objects.create(person=record, requested_by=request.user, result=result)
    audit(request, 'TEXT_ASSISTANCE_REQUESTED', record, new={'generation_id': str(item.pk), 'reason': reason})
    return Response({'id': str(item.pk), 'result': result}, status=201)


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
def workspace(request):
    if profile(request.user).role not in ['ADMIN', 'MANAGER', 'COUNSELOR', 'MANAGEMENT']:
        raise PermissionDenied('This role does not have an insights workspace.')
    qs = scoped_people(request.user, Person.objects.filter(archived_at__isnull=True, merged_into__isnull=True))
    active = qs.exclude(lead_status__in=['LOST', 'ON_HOLD'])
    now = timezone.now()
    ranked = active.annotate(overdue_count=Count('followups', filter=Q(followups__completed_at__isnull=True, followups__due_at__lt=now) & ~Q(followups__status='CANCELLED'), distinct=True),
        blocker_count=Count('admission_blockers', filter=Q(admission_blockers__resolved_at__isnull=True), distinct=True),
        deadline_count=Count('admission_deadlines', filter=Q(admission_deadlines__status='OPEN', admission_deadlines__due_at__lte=now+timezone.timedelta(days=7)), distinct=True),
        overdue_deadline_count=Count('admission_deadlines', filter=Q(admission_deadlines__status='OPEN', admission_deadlines__due_at__lt=now), distinct=True),
        missing_document_count=Count('admission_documents', filter=Q(admission_documents__required=True) & (~Q(admission_documents__status__in=['VERIFIED','WAIVED','NOT_APPLICABLE']) | Q(admission_documents__status='VERIFIED', admission_documents__expires_at__lte=now)), distinct=True))
    from django.db.models import F, ExpressionWrapper, IntegerField, Case, When, Value
    from django.db.models.functions import Least
    ranked = ranked.annotate(rank=Least(Value(100), ExpressionWrapper(F('overdue_count')*30+F('blocker_count')*25+F('deadline_count')*20+F('overdue_deadline_count')*20+F('missing_document_count')*10+Case(When(stage='LEAD', next_action_due_at__isnull=True, then=Value(15)), default=Value(0)), output_field=IntegerField()))).order_by('-rank', 'created_at', 'pk')
    offset = serializers.IntegerField(min_value=0).run_validation(request.query_params.get('offset', 0))
    priorities = [{'id': str(p.pk), 'ref': p.ref, 'name': p.full_name, **priority(p)} for p in ranked[offset:offset+30]]
    applications = Application.objects.filter(person__in=qs)
    anomalies = []
    for name, query in [('Active lead without an owner', active.filter(owner__isnull=True, stage='LEAD')),
        ('Active lead without a next action', active.filter(next_action_due_at__isnull=True, stage='LEAD'))]:
        anomalies.append({'rule': name, 'count': query.count(), 'examples': list(query.order_by('created_at').values('id', 'ref', 'full_name')[:10])})
    anomalies.append({'rule': 'Verified document expired', 'count': Document.objects.filter(person__in=qs, status='VERIFIED', expires_at__lte=now).count(),
        'examples': list(Document.objects.filter(person__in=qs, status='VERIFIED', expires_at__lte=now).values('id', 'title', 'person__ref')[:10])})
    return Response({'generated_at': now, 'count': active.count(), 'offset': offset,
        'priorities': priorities, 'anomalies': anomalies,
        'pipeline': list(qs.values('stage', 'lead_status').annotate(count=Count('pk')).order_by('stage', 'lead_status')),
        'application_states': list(applications.values('state').annotate(count=Count('pk')).order_by('state')),
        'overdue_followups': FollowUp.objects.filter(person__in=qs, completed_at__isnull=True, due_at__lt=now).exclude(status='CANCELLED').count(),
        'open_deadlines': Deadline.objects.filter(person__in=qs, status='OPEN').count(),
        'scope_notice': 'Counts and examples follow your record access. Priority weights: overdue follow-up 30, blocker 25, deadline within seven days 20, overdue deadline 40, incomplete required document 10, missing next action 15. Scores capped at 100. Queue ordering uses these weights across the complete accessible active record scope; ties use record creation time.'})
