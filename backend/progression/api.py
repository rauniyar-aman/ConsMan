from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import F,Max,Sum,Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError,PermissionDenied
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from crm.models import Task
from crm.permissions import profile,scope
from crm.operations import safe_date,required_reason
from crm.services import audit,activity,refresh_next_action
from crm.idempotency import idempotent
from admissions.api import get_app,apps,doc_data
from admissions.models import Document,Deadline,Blocker,OfferReceipt
from admissions.services import event,blockers
from .models import *

VISA_STATES=['PREPARING','READY','SUBMITTED','BIOMETRICS_PENDING','INTERVIEW','ADMIN_PROCESSING','APPROVED','REFUSED','WITHDRAWN']


def ref(kind):
    year=timezone.localdate().year
    counter,_=JourneyCounter.objects.get_or_create(kind=kind,year=year)
    JourneyCounter.objects.filter(pk=counter.pk).update(value=F('value')+1);counter.refresh_from_db()
    return f'{kind}-{year}-{counter.value:06d}'


def string(data,key,length=160,blank=False):return serializers.CharField(max_length=length,allow_blank=blank).run_validation(data.get(key,''))
def amount(data,key='amount',digits=12):return serializers.DecimalField(max_digits=digits,decimal_places=2,min_value=Decimal('0.01')).run_validation(data.get(key))
def currency(data):return serializers.RegexField(r'^[A-Za-z]{3}$').run_validation(data.get('currency')).upper()
def identifier(data,key='id',uuid=False):return (serializers.UUIDField() if uuid else serializers.IntegerField(min_value=1)).run_validation(data.get(key))
def date_only(data,key):return serializers.DateField().run_validation(data.get(key))
def document(app,value):return get_object_or_404(app.documents,pk=serializers.UUIDField().run_validation(value))
def reviewed_document(doc):return doc.status=='VERIFIED' and (not doc.expires_at or doc.expires_at>timezone.now())
def visa_event(request,case,kind,old=None,new=None,reason=''):
    VisaEvent.objects.create(case=case,type=kind,old=old or {},new=new or {},reason=reason,actor=request.user)
    event(request,case.application,'VISA_'+kind,old=old,new={'case_id':str(case.pk),**(new or {})},reason=reason)
def value_rows(qs):return list(qs.values())
def verified_deposit(app,code):
    return app.payments.filter(status='VERIFIED',currency=code,purpose='DEPOSIT',proof__status='VERIFIED').filter(Q(proof__expires_at__isnull=True)|Q(proof__expires_at__gt=timezone.now())).aggregate(total=Sum('amount'))['total'] or Decimal(0)


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
@idempotent()
def workflows(request):
    scope(request.user,'view')
    if request.method=='GET':return Response({'results':value_rows(VisaWorkflow.objects.order_by('country','-version'))})
    if profile(request.user).role!='ADMIN':raise PermissionDenied('Only administrators create visa workflow versions.')
    country=string(request.data,'country',80)
    stages=serializers.ListField(child=serializers.ChoiceField(choices=VISA_STATES),min_length=2,max_length=9).run_validation(request.data.get('milestones'))
    if stages!=[state for state in VISA_STATES if state in stages] or stages[0]!='PREPARING' or len(set(stages))!=len(stages) or not all(s in stages for s in ['READY','SUBMITTED','APPROVED','REFUSED','WITHDRAWN']):raise ValidationError('Start with Preparing and include Ready, Submitted and all decision states.')
    required=serializers.ListField(child=serializers.CharField(max_length=160),max_length=50).run_validation(request.data.get('required_documents',[]))
    predeparture=serializers.ListField(child=serializers.CharField(max_length=160),max_length=50).run_validation(request.data.get('predeparture_checklist',[]))
    counter,_=JourneyCounter.objects.get_or_create(kind='WORKFLOW',year=0)
    JourneyCounter.objects.select_for_update().get(pk=counter.pk)
    version=(VisaWorkflow.objects.filter(country__iexact=country).aggregate(v=Max('version'))['v'] or 0)+1
    workflow=VisaWorkflow.objects.create(country=country,version=version,milestones=stages,required_documents=required,predeparture_checklist=predeparture,financial_evidence_required=serializers.BooleanField().run_validation(request.data.get('financial_evidence_required',True)),created_by=request.user)
    audit(request,'VISA_WORKFLOW_CREATED',workflow,new={'country':country,'version':version})
    return Response({'id':workflow.pk,'version':version},status=201)


@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['GET','POST'])
@transaction.atomic
@idempotent()
def journey(request,pk):
    app=get_app(request,pk,request.method=='POST')
    if request.method=='POST':
        data=request.data;action=data.get('action')
        if action=='condition':
            condition=get_object_or_404(OfferCondition,pk=identifier(data),offer__application=app)
            state=data.get('status')
            if state not in ['PENDING','MET','WAIVED']:raise ValidationError('Invalid condition status.')
            condition.document=document(app,data['document_id']) if data.get('document_id') else None
            if state=='MET' and condition.document and not reviewed_document(condition.document):raise ValidationError('Verify supporting evidence before meeting the condition.')
            condition.status=state;condition.reason=required_reason(data);condition.reviewed_by=request.user;condition.reviewed_at=timezone.now();condition.save()
            event(request,app,'OFFER_CONDITION_REVIEWED',new={'condition_id':condition.pk,'status':state},reason=condition.reason)
        elif action=='accept_offer':
            offer=get_object_or_404(OfferReceipt.objects.select_for_update(),pk=identifier(data,'offer_id'),application=app,status='RECEIVED')
            if app.state!='OFFER_RECEIVED':raise ValidationError('The application must be at Offer received.')
            if offer.expires_at and offer.expires_at<=timezone.now():raise ValidationError('This offer has expired.')
            if offer.tracked_conditions.exclude(status__in=['MET','WAIVED']).exists():raise ValidationError('Resolve all offer conditions before acceptance.')
            reason=required_reason(data);offer.status='ACCEPTED';offer.save(update_fields=['status']);app.state='OFFER_ACCEPTED';app.save(update_fields=['state','updated_at'])
            event(request,app,'OFFER_ACCEPTED',new={'offer_id':offer.pk},reason=reason)
            app.deadlines.filter(source='OFFER',status='OPEN').update(status='COMPLETED',completed_at=timezone.now())
        elif action=='deposit':
            total=amount(data);code=currency(data);due=safe_date(data['due_at']) if data.get('due_at') else None
            reason=required_reason(data)
            DepositRequirement.objects.update_or_create(application=app,defaults={'amount':total,'currency':code,'due_at':due,'waived':serializers.BooleanField().run_validation(data.get('waived',False)),'reason':reason})
            app.deadlines.filter(type='DEPOSIT',source='DEPOSIT',status='OPEN').update(status='SUPERSEDED')
            if due and not data.get('waived'):Deadline.objects.create(person=app.person,application=app,type='DEPOSIT',title='Institution deposit',due_at=due,source='DEPOSIT')
            event(request,app,'DEPOSIT_REQUIREMENT',new={'amount':str(total),'currency':code},reason=reason)
        elif action=='payment':
            proof=document(app,data['proof_id']) if data.get('proof_id') else None
            method=serializers.ChoiceField(choices=['CASH','BANK_TRANSFER','CARD','ONLINE','OTHER']).run_validation(data.get('method'))
            payment=Payment.objects.create(ref=ref('PAY'),person=app.person,application=app,amount=amount(data),currency=currency(data),paid_on=date_only(data,'paid_on'),method=method,reference=string(data,'reference',120,True),proof=proof,created_by=request.user)
            event(request,app,'PAYMENT_RECORDED',new={'payment_id':str(payment.pk),'ref':payment.ref,'amount':str(payment.amount),'currency':payment.currency})
        elif action=='payment_status':
            payment=get_object_or_404(Payment.objects.select_for_update(),pk=identifier(data,uuid=True),application=app)
            state=data.get('status');allowed={'PENDING':['RECEIVED','CANCELLED'],'RECEIVED':['VERIFIED','CANCELLED'],'VERIFIED':['REFUNDED']}
            if state not in allowed.get(payment.status,[]):raise ValidationError('Invalid payment transition.')
            reason=required_reason(data)
            if state in ['VERIFIED','REFUNDED'] and profile(request.user).role not in ['ADMIN','MANAGER']:raise PermissionDenied('An administrator or manager must verify or refund payments.')
            if state=='VERIFIED':
                if data.get('proof_id'):payment.proof=document(app,data['proof_id'])
                if not payment.proof or not reviewed_document(payment.proof):raise ValidationError('Attach a verified proof document before verifying payment.')
                payment.verified_by=request.user
            payment.status=state;payment.save();event(request,app,'PAYMENT_STATUS',new={'payment_id':str(payment.pk),'status':state},reason=reason)
        elif action=='enrollment_document':
            doc=document(app,data.get('document_id'))
            kind=serializers.ChoiceField(choices=['CAS','COE','I20','LOA','OTHER']).run_validation(data.get('kind'));number=string(data,'number',120)
            if app.enrollment_documents.filter(kind=kind,number=number).exists():raise ValidationError('This enrollment document is already recorded.')
            item=EnrollmentDocument.objects.create(application=app,kind=kind,number=number,issued_on=date_only(data,'issued_on'),expires_at=safe_date(data['expires_at']) if data.get('expires_at') else None,document=doc)
            event(request,app,'ENROLLMENT_DOCUMENT_RECEIVED',new={'id':item.pk,'kind':item.kind})
        elif action=='verify_enrollment_document':
            item=get_object_or_404(EnrollmentDocument,pk=identifier(data),application=app)
            if not reviewed_document(item.document) or item.expires_at and item.expires_at<=timezone.now():raise ValidationError('Verify an unexpired supporting document first.')
            item.status='VERIFIED';item.reviewed_by=request.user;item.save();event(request,app,'ENROLLMENT_DOCUMENT_VERIFIED',new={'id':item.pk})
        elif action=='visa_create':
            if app.state!='OFFER_ACCEPTED':raise ValidationError('Accept the offer before starting a visa case.')
            previous=app.visa_cases.order_by('-attempt_no').first()
            if previous and previous.state not in ['REFUSED','WITHDRAWN']:raise ValidationError('A current visa attempt already exists.')
            workflow=get_object_or_404(VisaWorkflow,pk=identifier(data,'workflow_id'),active=True)
            if workflow.country.casefold()!=app.course_offering.campus.university.country.casefold():raise ValidationError('Select the visa workflow for the application destination.')
            snapshot={'id':workflow.pk,'country':workflow.country,'version':workflow.version,'milestones':workflow.milestones,'required_documents':workflow.required_documents,'financial_evidence_required':workflow.financial_evidence_required,'predeparture_checklist':workflow.predeparture_checklist}
            case=VisaCase.objects.create(ref=ref('VISA'),application=app,attempt_no=(previous.attempt_no+1 if previous else 1),previous_case=previous,workflow_snapshot=snapshot,created_by=request.user)
            PreDeparture.objects.get_or_create(application=app,defaults={'checklist':[{'title':title,'status':'PENDING'} for title in workflow.predeparture_checklist]})
            for index,title in enumerate(workflow.required_documents):
                doc=Document.objects.create(person=app.person,application=app,type=f'VISA_{case.attempt_no}_{index+1}',title=title,required=True)
                VisaDocument.objects.create(case=case,document=doc)
            visa_event(request,case,'CREATED',new={'ref':case.ref,'attempt_no':case.attempt_no,'workflow_version':workflow.version})
        elif action in ['visa_state','visa_appointment','visa_information','visa_blocker','financial_evidence','financial_review']:
            if app.state!='OFFER_ACCEPTED':raise ValidationError('Visa work requires an active accepted application.')
            case=get_object_or_404(VisaCase.objects.select_for_update(),pk=identifier(data,'case_id',uuid=True),application=app)
            if case.state in ['APPROVED','REFUSED','WITHDRAWN']:raise ValidationError('Visa decisions are final. Create a new attempt after refusal or withdrawal.')
            if action=='visa_state':
                state=data.get('state');stages=case.workflow_snapshot['milestones'];reason=required_reason(data)
                if state not in stages or state=='PREPARING':raise ValidationError('Invalid visa milestone.')
                if state in ['READY','SUBMITTED']:
                    if case.checklist_documents.filter(document__required=True).exclude(document__status__in=['VERIFIED','WAIVED','NOT_APPLICABLE']).exists() or case.checklist_documents.filter(document__status='VERIFIED',document__expires_at__lte=timezone.now()).exists():raise ValidationError('Complete and verify the visa checklist first.')
                    if blockers(app):raise ValidationError('Resolve application/person/visa blockers first.')
                    if case.workflow_snapshot.get('financial_evidence_required') and (not case.financial_evidence.filter(status='VERIFIED').exists() or case.financial_evidence.exclude(status='VERIFIED',document__status='VERIFIED').exists() or case.financial_evidence.filter(Q(expires_at__lte=timezone.now())|Q(document__expires_at__lte=timezone.now())).exists()):raise ValidationError('Verify unexpired financial evidence first.')
                progress=[s for s in stages if s not in ['APPROVED','REFUSED','WITHDRAWN']]
                if state in ['APPROVED','REFUSED'] and not case.submitted_at:raise ValidationError('Submit the visa case before recording a decision.')
                if state in progress and progress.index(state)!=progress.index(case.state)+1:raise ValidationError('Advance one configured visa milestone at a time.')
                old=case.state;case.state=state
                if state=='SUBMITTED':case.submitted_at=timezone.now()
                if state in ['APPROVED','REFUSED','WITHDRAWN']:case.decided_at=timezone.now();case.decision_reason=reason
                case.save();visa_event(request,case,'STATE_CHANGED',old={'state':old},new={'state':state},reason=reason)
            elif action=='visa_appointment':
                case.appointment_at=safe_date(data.get('appointment_at'));case.save(update_fields=['appointment_at'])
                app.deadlines.filter(type='VISA_APPOINTMENT',title=f'{case.ref} appointment',status='OPEN').update(status='SUPERSEDED')
                Deadline.objects.create(person=app.person,application=app,type='VISA_APPOINTMENT',title=f'{case.ref} appointment',due_at=case.appointment_at,source='VISA')
                visa_event(request,case,'APPOINTMENT',new={'appointment_at':case.appointment_at.isoformat()},reason=required_reason(data))
            elif action=='visa_information':
                title=string(data,'title',160);due=safe_date(data.get('due_at'));reason=required_reason(data)
                doc=Document.objects.create(person=app.person,application=app,type=f'VISA_INFO_{case.pk}_{case.events.count()}',title=title)
                VisaDocument.objects.create(case=case,document=doc)
                Task.objects.create(person=app.person,owner=app.owner,title=f'{case.ref}: {title}',due_at=due)
                Deadline.objects.create(person=app.person,application=app,document=doc,type='DOCUMENT',title=title,due_at=due,source='VISA_INFORMATION')
                refresh_next_action(app.person);visa_event(request,case,'ADDITIONAL_INFORMATION',new={'document_id':str(doc.pk)},reason=reason)
            elif action=='visa_blocker':
                blocker=Blocker.objects.create(person=app.person,application=app,type=string(data,'type',60),description=string(data,'description',2000),assigned_to=app.owner,created_by=request.user)
                VisaBlocker.objects.create(case=case,blocker=blocker);visa_event(request,case,'BLOCKER',new={'blocker_id':blocker.pk})
            elif action=='financial_evidence':
                evidence=FinancialEvidence.objects.create(case=case,document=document(app,data.get('document_id')),kind=string(data,'kind',40),holder=string(data,'holder',160),amount=amount(data,digits=14),currency=currency(data),statement_on=date_only(data,'statement_on'),expires_at=safe_date(data['expires_at']) if data.get('expires_at') else None)
                visa_event(request,case,'FINANCIAL_EVIDENCE',new={'id':evidence.pk})
            else:
                evidence=get_object_or_404(FinancialEvidence,pk=identifier(data),case=case)
                state=serializers.ChoiceField(choices=['VERIFIED','REJECTED']).run_validation(data.get('status'))
                if state=='VERIFIED' and (not reviewed_document(evidence.document) or evidence.expires_at and evidence.expires_at<=timezone.now()):raise ValidationError('Verify the unexpired financial document first.')
                evidence.status=state;evidence.reviewed_by=request.user;evidence.reason=required_reason(data);evidence.save();visa_event(request,case,'FINANCIAL_REVIEW',new={'id':evidence.pk,'status':state},reason=evidence.reason)
        elif action=='predeparture':
            if app.state not in ['OFFER_ACCEPTED','ENROLLED']:raise ValidationError('Accept the offer before pre-departure planning.')
            plan,_=PreDeparture.objects.get_or_create(application=app)
            for key,length in [('accommodation',2000),('airport_pickup',255),('flight_number',40),('notes',2000)]:
                if key in data:setattr(plan,key,string(data,key,length,True))
            for key in ['departure_at','arrival_at','arrived_at']:
                if key in data:setattr(plan,key,safe_date(data[key]) if data[key] else None)
            if plan.departure_at and plan.arrival_at and plan.arrival_at<plan.departure_at:raise ValidationError('Arrival must follow departure.')
            if 'checklist' in data:
                checklist=data['checklist']
                if not isinstance(checklist,list) or len(checklist)>50 or any(not isinstance(x,dict) or not isinstance(x.get('title'),str) or not 1<=len(x['title'])<=160 or x.get('status') not in ['PENDING','DONE','WAIVED'] for x in checklist):raise ValidationError('Checklist needs titles and Pending, Done or Waived statuses.')
                plan.checklist=checklist
            plan.save();event(request,app,'PREDEPARTURE_UPDATED',new={'checklist':plan.checklist},reason=required_reason(data))
        elif action=='enroll':
            if app.state!='OFFER_ACCEPTED' or not app.offers.filter(status='ACCEPTED').exists():raise ValidationError('An accepted offer is required before final enrollment.')
            if blockers(app):raise ValidationError('Resolve blockers before enrollment.')
            for title in app.workflow_template.enrollment_requirements:
                if not app.enrollment_documents.filter(Q(kind__iexact=title.replace('-',''))|Q(document__title__iexact=title)).exists():raise ValidationError(f'Record the required enrollment document: {title}. Use its document kind or matching checklist title.')
            if app.enrollment_documents.exclude(status='VERIFIED',document__status='VERIFIED').exists() or app.enrollment_documents.filter(Q(expires_at__lte=timezone.now())|Q(document__expires_at__lte=timezone.now())).exists():raise ValidationError('Verify unexpired enrollment documents first.')
            if app.workflow_template.visa_requirements and not app.visa_cases.exists():raise ValidationError('Complete the required visa journey before enrollment.')
            if app.visa_cases.exists() and not app.visa_cases.filter(state='APPROVED').exists():raise ValidationError('An approved visa is required for this journey.')
            deposit=DepositRequirement.objects.filter(application=app).first()
            if deposit and not deposit.waived:
                paid=verified_deposit(app,deposit.currency)
                if paid<deposit.amount:raise ValidationError('Verify the required institution deposit first.')
            enrollment=Enrollment.objects.create(application=app,enrolled_on=date_only(data,'enrolled_on'),institution_reference=string(data,'institution_reference',120),confirmed_by=request.user)
            app.state='ENROLLED';app.save(update_fields=['state','updated_at'])
            person=app.person;old=person.stage;person.stage='STUDENT';person.student_state='ENROLLED';person.converted_at=person.converted_at or timezone.now();person.save()
            reason=required_reason(data);event(request,app,'ENROLLMENT_CONFIRMED',new={'id':enrollment.pk,'institution_reference':enrollment.institution_reference},reason=reason);audit(request,'PERSON_ENROLLED',person,old={'stage':old},new={'stage':'STUDENT','application_id':str(app.pk)});activity(request,person,'Institution enrollment confirmed',reason,'ENROLLMENT')
        else:raise ValidationError('Unknown journey action.')
    deposit=DepositRequirement.objects.filter(application=app).first()
    paid=verified_deposit(app,deposit.currency if deposit else 'USD')
    cases=[]
    for case in app.visa_cases.order_by('-attempt_no'):
        cases.append({'id':str(case.pk),'ref':case.ref,'attempt_no':case.attempt_no,'previous_case_id':case.previous_case_id,'state':case.state,'workflow':case.workflow_snapshot,'appointment_at':case.appointment_at,'submitted_at':case.submitted_at,'decided_at':case.decided_at,'decision_reason':case.decision_reason,'documents':[doc_data(link.document) for link in case.checklist_documents.select_related('document').prefetch_related('document__versions')],'events':value_rows(case.events.order_by('-created_at')),'financial_evidence':value_rows(case.financial_evidence.all()),'blockers':value_rows(Blocker.objects.filter(visablocker__case=case))})
    return Response({'application_id':str(app.pk),'state':app.state,'can_edit':profile(request.user).role in ['ADMIN','MANAGER','COUNSELOR'],'can_verify_payment':profile(request.user).role in ['ADMIN','MANAGER'],'conditions':value_rows(OfferCondition.objects.filter(offer__application=app)),'offers':value_rows(app.offers.all()),'deposit':value_rows(DepositRequirement.objects.filter(application=app))[0] if deposit else None,'verified_deposit_total':str(paid),'payments':value_rows(app.payments.all()),'enrollment_documents':value_rows(app.enrollment_documents.all()),'visa_cases':cases,'predeparture':value_rows(PreDeparture.objects.filter(application=app)),'enrollment':value_rows(Enrollment.objects.filter(application=app))})
