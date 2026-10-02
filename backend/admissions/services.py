from datetime import timedelta
from decimal import Decimal,InvalidOperation
from django.db import transaction
from django.db.models import F,Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from crm.models import Task
from crm.services import audit,refresh_next_action
from .models import AdmissionCounter,ApplicationEvent,ApplicationTask,Deadline,Document

PHASE_TWO_STATES=['DRAFT','DOCUMENT_COLLECTION','READY','SUBMITTED','UNDER_ASSESSMENT','OFFER_RECEIVED']
TERMINAL_STATES=['REJECTED','OFFER_DECLINED','OFFER_EXPIRED','WITHDRAWN','DEFERRED','CANCELLED']
DOCUMENT_STATES=['REQUESTED','UPLOADED','UNDER_REVIEW','VERIFIED','REJECTED','RESUBMISSION_REQUIRED','EXPIRED','WAIVED','NOT_APPLICABLE']
DEADLINE_TYPES=['APPLICATION','DOCUMENT','OFFER_EXPIRY','DEPOSIT','ENROLLMENT_DOC','VISA_APPOINTMENT','VISA_SUBMISSION','COURSE_START','PRE_DEPARTURE','FLIGHT']


@transaction.atomic
def next_reference():
    year=timezone.localdate().year
    row,_=AdmissionCounter.objects.get_or_create(year=year)
    AdmissionCounter.objects.filter(pk=row.pk).update(value=F('value')+1)
    row.refresh_from_db()
    return f'APP-{year}-{row.value:06d}'


def event(request,app,kind,old=None,new=None,reason=''):
    ApplicationEvent.objects.create(application=app,type=kind,actor=request.user,old=old or {},new=new or {},reason=reason)
    audit(request,'ADMISSION_'+kind,app,old=old,new=new)


def generate_tasks(app,state):
    for title in app.workflow_template.task_templates.get(state,[]):
        if ApplicationTask.objects.filter(application=app,milestone=state,task__title=title).exists():continue
        task=Task.objects.create(person=app.person,owner=app.owner,title=title,due_at=timezone.now()+timedelta(days=1))
        ApplicationTask.objects.create(application=app,task=task,milestone=state)
    refresh_next_action(app.person)


def initialize_application(request,app):
    for definition in app.workflow_template.required_documents:
        Document.objects.create(person=app.person,application=app,type=definition['type'],title=definition['title'],required=definition.get('required',True))
    if app.course_offering.intake.application_deadline:
        Deadline.objects.create(person=app.person,application=app,type='APPLICATION',title='Application submission',due_at=app.course_offering.intake.application_deadline,source='INTAKE')
    due=timezone.make_aware(__import__('datetime').datetime.combine(app.course_offering.intake.start_date,__import__('datetime').time(10)))
    Deadline.objects.create(person=app.person,application=app,type='COURSE_START',title='Course starts',due_at=due,source='INTAKE')
    event(request,app,'CREATED',new={'ref':app.ref,'offering_id':app.course_offering_id,'workflow_version':app.workflow_template.version})
    generate_tasks(app,'DRAFT')


def blockers(app):
    return app.person.admission_blockers.filter(resolved_at__isnull=True).filter(Q(application=app)|Q(application__isnull=True)).exists()


def incomplete_documents(app):
    now=timezone.now()
    return app.documents.filter(required=True).filter(~Q(status__in=['VERIFIED','WAIVED','NOT_APPLICABLE'])|Q(status='VERIFIED',expires_at__lte=now)).exists()


def transition(request,app,state,reason=''):
    milestones=app.workflow_template.milestones
    if state not in milestones+TERMINAL_STATES:raise ValidationError('This state is not enabled in the application workflow.')
    if state==app.state:return
    if app.state in TERMINAL_STATES:raise ValidationError('Terminal applications cannot advance; create a new application or use reviewed deferral.')
    if state in ['READY','SUBMITTED'] and (blockers(app) or incomplete_documents(app)):raise ValidationError('Resolve blockers and verify, waive or mark required documents not applicable before proceeding.')
    if state=='OFFER_RECEIVED' and not app.offers.exists():raise ValidationError('Record an offer before moving to offer received.')
    if state in TERMINAL_STATES or milestones.index(state)!=milestones.index(app.state)+1:
        if not reason.strip():raise ValidationError('A reason is required for terminal, skipped or backward transitions.')
    old=app.state;app.state=state;app.save(update_fields=['state','updated_at'])
    event(request,app,'STATE_CHANGED',old={'state':old},new={'state':state},reason=reason)
    generate_tasks(app,state)


def matching(person,preference,offerings):
    results=[]
    countries=preference.countries if preference else person.preferred_countries
    levels=preference.levels if preference else person.study_levels
    course_ids=set(preference.courses.values_list('pk',flat=True)) if preference else set()
    intake_ids=set(preference.intakes.values_list('pk',flat=True)) if preference else set()
    for offering in offerings:
        reasons=[];unknown=[];failed=[]
        if countries and offering.campus.university.country not in countries:failed.append('Destination does not match preferences')
        if levels and offering.course.level not in levels:failed.append('Study level does not match preferences')
        if preference:
            if course_ids and offering.course_id not in course_ids:failed.append('Course does not match preferences')
            if intake_ids and offering.intake_id not in intake_ids:failed.append('Intake does not match preferences')
            if preference.maximum_fee is not None:
                if preference.currency!=offering.currency:unknown.append('Fee currency differs; no currency conversion assumed')
                elif offering.fee>preference.maximum_fee:failed.append('Fee exceeds budget')
                else:reasons.append('Within fee budget')
        if offering.minimum_academic_percent is not None:
            score=preference.academic_percent if preference else None
            if score is None:unknown.append('Academic percentage not provided')
            elif score<offering.minimum_academic_percent:failed.append('Academic percentage below requirement')
            else:reasons.append('Meets academic percentage requirement')
        if offering.minimum_english_score is not None:
            scores=[]
            for value in person.test_scores.filter(test=offering.english_test,status='TAKEN').values_list('score',flat=True):
                try:scores.append(Decimal(value))
                except InvalidOperation:continue
            if not scores:unknown.append('Required English test score not provided')
            elif max(scores)<offering.minimum_english_score:failed.append('English score below requirement')
            else:reasons.append('Meets English score requirement')
        if offering.intake.application_deadline and offering.intake.application_deadline<timezone.now():failed.append('Application deadline passed')
        results.append({'offering_id':offering.pk,'course':offering.course.name,'university':offering.campus.university.name,'campus':offering.campus.name,'country':offering.campus.university.country,'intake':offering.intake.name,'fee':str(offering.fee),'currency':offering.currency,'result':'NOT_MATCHED' if failed else 'REVIEW_REQUIRED' if unknown else 'MATCHED','reasons':reasons,'missing_information':unknown,'mismatches':failed,'scholarships':[{'id':s.pk,'name':s.name,'amount':str(s.amount),'eligibility':s.eligibility,'eligible':None if s.minimum_academic_percent is not None and (not preference or preference.academic_percent is None) else s.minimum_academic_percent is None or preference.academic_percent>=s.minimum_academic_percent} for s in offering.scholarships.all() if s.active and (not s.deadline or s.deadline>=timezone.now())]})
    return sorted(results,key=lambda r:['MATCHED','REVIEW_REQUIRED','NOT_MATCHED'].index(r['result']))
