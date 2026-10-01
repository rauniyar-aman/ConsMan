from django.db.models import Count
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from .models import Person,FollowUp,SlaTimer
from .permissions import scoped_people,branch_scope,scope

@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
def reports(request):
    scope(request.user,'export')
    people=scoped_people(request.user,Person.objects.filter(archived_at__isnull=True))
    from intake.models import IntakeSubmission
    intake=branch_scope(request.user,IntakeSubmission.objects.all(),'export',field='qr__branch') if scope(request.user,'export') in ['full','branch','reports'] else IntakeSubmission.objects.none()
    now=timezone.now()
    return Response({'people':people.count(),'active_leads':people.filter(stage='LEAD').exclude(lead_status='LOST').count(),'students':people.filter(stage='STUDENT').count(),'unassigned':people.filter(owner__isnull=True).count(),'overdue':FollowUp.objects.filter(person__in=people,status__in=['OPEN','IN_PROGRESS'],due_at__lt=now).count(),'sla_breaches':SlaTimer.objects.filter(person__in=people,satisfied_at__isnull=True,due_at__lt=now).count(),'unverified_intake':intake.filter(status='UNVERIFIED').count(),'sources':list(people.values('source__name').annotate(total=Count('id')).order_by('-total')),'owners':list(people.values('owner__first_name','owner__last_name').annotate(total=Count('id')).order_by('-total')),'funnel':list(people.values('lead_status').annotate(total=Count('id')).order_by('lead_status'))})
