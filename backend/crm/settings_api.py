from datetime import date
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_time
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from .models import *
from .permissions import scope, profile, MATRIX
from .services import audit, notify
from .calendar import recompute_calendar

MASTERS={'sources':Source,'campaigns':Campaign,'source_details':SourceDetail,'tags':Tag,'lost_reasons':LostReason,'branches':Branch}
POLICIES={'stale_days':(1,3650),'unverified_retention_days':(1,90),'resolved_intake_retention_days':(1,365),'closed_lead_retention_months':(1,120),'otp_valid_minutes':(1,10),'otp_max_attempts':(1,5),'otp_cooldown_seconds':(60,600),'otp_max_sends':(1,3),'otp_phone_daily_limit':(1,20),'otp_ip_daily_limit':(1,100),'otp_qr_daily_limit':(1,10000),'otp_global_daily_limit':(1,100000),'otp_monthly_budget_minor':(0,100000000)}

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST','PATCH'])
@transaction.atomic
def settings_home(request):
    staff=profile(request.user)
    if request.method=='GET':
        if staff.role not in ['ADMIN','MANAGER']:raise PermissionDenied()
        branches=Branch.objects.all() if staff.role=='ADMIN' else Branch.objects.filter(pk=staff.branch_id)
        users=StaffProfile.objects.filter(branch__in=branches).select_related('user')
        return Response({'matrix':MATRIX,'users':[{'id':s.user_id,'username':s.user.username,'name':s.user.get_full_name(),'role':s.role,'branch_id':s.branch_id,'active':s.user.is_active,'availability':s.availability,'leave_until':s.leave_until,'max_open_leads':s.max_open_leads,'mfa_enabled':MfaDevice.objects.filter(user=s.user,confirmed=True).exists()} for s in users],'calendars':list(BusinessCalendar.objects.filter(branch__in=branches).values()),'sla_rules':list(SlaRule.objects.values()),'assignment_rules':list(AssignmentRule.objects.filter(branch__in=branches).values()),'policies':list(SystemPolicy.objects.filter(key__in=POLICIES).values('key','value')),'masters':{key:list(model.objects.values()) for key,model in MASTERS.items()},'messaging':{'gateway_configured':bool(__import__('django.conf',fromlist=['settings']).settings.WHATSAPP_GATEWAY_URL),'sms_configured':bool(__import__('django.conf',fromlist=['settings']).settings.SMS_PROVIDER_URL)}})
    scope(request.user,'settings')
    kind=request.data.get('kind')
    data=request.data.get('data',{})
    if not isinstance(data,dict):raise ValidationError('Data must be an object.')
    identifier=request.data.get('id')
    if kind in MASTERS:
        model=MASTERS[kind]
        fields=['name','active'] if kind not in ['branches','campaigns','source_details','lost_reasons'] else {'branches':['name','code'],'campaigns':['name','source','active'],'source_details':['name','campaign','active'],'lost_reasons':['name','active','note_required']}[kind]
        if set(data)-set(fields):raise ValidationError('Unsupported master fields.')
        Serializer=type('MasterSerializer',(serializers.ModelSerializer,),{'Meta':type('Meta',(),{'model':model,'fields':fields})})
        obj=get_object_or_404(model,pk=identifier) if identifier else None
        ser=Serializer(obj,data=data,partial=bool(obj));ser.is_valid(raise_exception=True);obj=ser.save()
        if model==Branch:
            BusinessCalendar.objects.get_or_create(branch=obj);AssignmentRule.objects.get_or_create(branch=obj)
        audit(request,'MASTER_CHANGED',SimpleNamespace(id=obj.pk,branch=staff.branch),new={'kind':kind,'data':ser.data})
        return Response({'id':obj.pk})
    if kind=='calendar':
        calendar=get_object_or_404(BusinessCalendar.objects.select_for_update(),pk=identifier)
        if set(data)-{'working_days','open_time','close_time','holidays','timezone'}:raise ValidationError('Unsupported calendar fields.')
        days=data.get('working_days',calendar.working_days)
        if not isinstance(days,list) or not days or any(type(d)!=int or d not in range(7) for d in days):raise ValidationError('Choose at least one weekday (0=Monday, 6=Sunday).')
        opening=parse_time(data.get('open_time',str(calendar.open_time)));closing=parse_time(data.get('close_time',str(calendar.close_time)))
        if not opening or not closing or opening>=closing:raise ValidationError('Closing time must be after opening time.')
        holidays=data.get('holidays',calendar.holidays)
        if not isinstance(holidays,list) or len(holidays)>1000:raise ValidationError('Use a list of holiday dates.')
        try:
            for day in holidays:date.fromisoformat(day)
            ZoneInfo(data.get('timezone',calendar.timezone))
        except (ValueError,TypeError,ZoneInfoNotFoundError):raise ValidationError('Invalid date or time zone.')
        calendar.working_days=days;calendar.open_time=opening;calendar.close_time=closing;calendar.holidays=holidays;calendar.timezone=data.get('timezone',calendar.timezone);calendar.save();recompute_calendar(calendar)
        audit(request,'CALENDAR_CHANGED',SimpleNamespace(id=calendar.pk,branch=calendar.branch),new=data)
        return Response({'ok':True})
    if kind=='sla':
        item=get_object_or_404(SlaRule,pk=identifier)
        hours=serializers.IntegerField(min_value=1,max_value=1000).run_validation(data.get('target_business_hours'))
        item.target_business_hours=hours
        if 'active' in data:item.active=serializers.BooleanField().run_validation(data['active'])
        item.save()
        for calendar in BusinessCalendar.objects.all():recompute_calendar(calendar)
        audit(request,'SLA_RULE_CHANGED',SimpleNamespace(id=item.pk,branch=staff.branch),new={'hours':hours,'active':item.active})
        return Response({'ok':True})
    if kind=='assignment':
        rule=get_object_or_404(AssignmentRule,pk=identifier)
        if data.get('mode') not in ['ROUND_ROBIN','MANAGER_QUEUE']:raise ValidationError('Invalid assignment mode.')
        rule.mode=data['mode'];rule.save()
        audit(request,'ASSIGNMENT_RULE_CHANGED',SimpleNamespace(id=rule.pk,branch=rule.branch),new=data)
        return Response({'ok':True})
    if kind=='policy':
        key=data.get('key')
        if key not in POLICIES:raise ValidationError('Unknown policy key.')
        low,high=POLICIES[key]
        value=serializers.IntegerField(min_value=low,max_value=high).run_validation(data.get('value'))
        SystemPolicy.objects.update_or_create(key=key,defaults={'value':value})
        audit(request,'POLICY_CHANGED',SimpleNamespace(id=key,branch=staff.branch),new={'value':value})
        return Response({'ok':True})
    if kind=='user':
        for flag in ['active','confirm_transfer']:
            if flag in data:data[flag]=serializers.BooleanField().run_validation(data[flag])
        user=get_object_or_404(User,pk=identifier) if identifier else User()
        old={'username':user.username,'name':user.get_full_name(),'active':user.is_active,'role':user.staff.role,'branch_id':user.staff.branch_id} if user.pk else {}
        if set(data)-{'username','name','role','branch_id','active','availability','leave_until','max_open_leads','password','confirm_transfer'}:raise ValidationError('Unsupported user fields.')
        role=data.get('role',getattr(getattr(user,'staff',None),'role','COUNSELOR'))
        if role not in StaffProfile.Role.values:raise ValidationError('Invalid role.')
        branch=get_object_or_404(Branch,pk=data.get('branch_id',getattr(getattr(user,'staff',None),'branch_id',staff.branch_id)))
        owns=Person.objects.filter(owner=user,archived_at__isnull=True).exclude(lead_status='LOST').exists() if user.pk else False
        if data.get('active') is False and owns:raise ValidationError('Reassign open records before deactivating this user.')
        if owns and (branch.pk!=user.staff.branch_id or role!='COUNSELOR') and not data.get('confirm_transfer'):raise ValidationError('Open record ownership must be confirmed or reassigned before branch/role transfer.')
        if user.pk==request.user.pk and (data.get('active') is False or role!='ADMIN'):raise ValidationError('You cannot remove your own administrative access.')
        username=serializers.CharField(max_length=150).run_validation(data.get('username',user.username))
        if User.objects.filter(username=username).exclude(pk=user.pk).exists():raise ValidationError('Username already exists.')
        name=str(data.get('name',user.get_full_name())).strip()
        if len(name)>160:raise ValidationError('Name is too long.')
        user.username=username;user.first_name=name.split(' ',1)[0];user.last_name=name.split(' ',1)[1] if ' ' in name else '';user.is_active=bool(data.get('active',True))
        if data.get('password'):
            try:validate_password(data['password'],user)
            except DjangoValidationError as e:raise ValidationError({'password':e.messages})
            user.set_password(data['password'])
        elif not user.pk:raise ValidationError('A password is required for a new user.')
        user.save()
        p,_=StaffProfile.objects.get_or_create(user=user,defaults={'role':role,'branch':branch})
        p.role=role;p.branch=branch
        if 'availability' in data:
            if data['availability'] not in ['ACTIVE','ON_LEAVE','INACTIVE']:raise ValidationError('Invalid availability.')
            p.availability=data['availability']
        if 'leave_until' in data:
            try:p.leave_until=date.fromisoformat(data['leave_until']) if data['leave_until'] else None
            except ValueError:raise ValidationError('Invalid leave date.')
        if 'max_open_leads' in data:p.max_open_leads=serializers.IntegerField(min_value=1,max_value=10000).run_validation(data['max_open_leads'])
        p.save()
        audit(request,'USER_PERMISSION_CHANGED',SimpleNamespace(id=user.pk,branch=branch),old=old,new={'username':user.username,'name':user.get_full_name(),'active':user.is_active,'role':p.role,'branch_id':p.branch_id,'availability':p.availability,'password_changed':bool(data.get('password'))})
        return Response({'id':user.pk})
    raise ValidationError('Unsupported settings kind.')

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
def propose_master(request):
    if profile(request.user).role not in ['MANAGER','ADMIN']:raise PermissionDenied()
    kind=request.data.get('kind');name=str(request.data.get('name','')).strip()
    if kind not in MASTERS or not name or len(name)>120:raise ValidationError('Choose a master type and a short proposal name.')
    for admin in StaffProfile.objects.filter(role='ADMIN',user__is_active=True).select_related('user'):notify(admin.user,'MASTER_PROPOSAL','Master-list proposal',message=f'{kind}: {name}')
    audit(request,'MASTER_PROPOSED',SimpleNamespace(id='proposal',branch=profile(request.user).branch),new={'kind':kind,'name':name})
    return Response({'ok':True})

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
@transaction.atomic
def team_member(request,pk):
    from .operations import object_person,required_reason
    person=object_person(request,pk,'reassign',True)
    user=get_object_or_404(User,pk=request.data.get('user_id'),is_active=True,staff__branch=person.branch)
    role=request.data.get('role_on_case','DOCUMENTATION')
    if role not in ['DOCUMENTATION','VISA','OTHER']:raise ValidationError('Invalid case role.')
    active=serializers.BooleanField().run_validation(request.data.get('active',True))
    item,_=TeamMember.objects.update_or_create(person=person,user=user,defaults={'role_on_case':role,'active':active})
    audit(request,'TEAM_CHANGED',person,new={'user_id':user.pk,'active':active,'reason':required_reason(request.data)})
    return Response({'id':item.pk})
