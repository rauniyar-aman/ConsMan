from datetime import timedelta
from django.db import DatabaseError, transaction
from django.test import TestCase
from django.utils import timezone
from admissions import tests as admission_fixtures
from admissions.models import Document, Application, Blocker
from crm.models import TeamMember
from .models import *


class ProgressionTests(TestCase):
    setUp=admission_fixtures.AdmissionTests.setUp
    user=admission_fixtures.AdmissionTests.user
    create=admission_fixtures.AdmissionTests.create
    patch=admission_fixtures.AdmissionTests.patch

    def act(self,app,action,**data):
        return self.client.post(f'/api/v1/progression/applications/{app.pk}/',{'action':action,**data},format='json')

    def accepted(self,conditions=None):
        app=self.create()
        response=self.client.post(f'/api/v1/admissions/applications/{app.pk}/offers/',{'type':'CONDITIONAL','conditions':conditions or []},format='json')
        self.assertEqual(response.status_code,201,response.data)
        for condition in OfferCondition.objects.filter(offer__application=app):
            self.assertEqual(self.act(app,'condition',id=condition.pk,status='WAIVED',reason='Institution confirmed exemption').status_code,200)
        response=self.act(app,'accept_offer',offer_id=response.data['id'],reason='Student accepted institution offer')
        self.assertEqual(response.status_code,200,response.data)
        app.refresh_from_db()
        return app

    def evidence(self,app):
        return Document.objects.create(person=app.person,application=app,type='EVIDENCE',title='Verified supporting file',status='VERIFIED',version=1)

    def visa_workflow(self,**data):
        return VisaWorkflow.objects.create(country='Testland',version=1,created_by=self.admin,milestones=['PREPARING','READY','SUBMITTED','APPROVED','REFUSED','WITHDRAWN'],**data)

    def case(self,app,workflow):
        r=self.act(app,'visa_create',workflow_id=workflow.pk)
        self.assertEqual(r.status_code,200,r.data)
        return VisaCase.objects.get(application=app,attempt_no=app.visa_cases.count())

    def test_offer_conditions_and_acceptance_guards(self):
        app=self.create()
        r=self.client.post(f'/api/v1/admissions/applications/{app.pk}/offers/',{'type':'CONDITIONAL','conditions':['Final transcript']},format='json')
        self.assertEqual(self.act(app,'accept_offer',offer_id=r.data['id'],reason='Accept').status_code,400)
        condition=OfferCondition.objects.get(offer__application=app)
        self.assertEqual(self.act(app,'condition',id=condition.pk,status='WAIVED').status_code,400)
        self.assertEqual(self.act(app,'condition',id=condition.pk,status='WAIVED',reason='Institution waiver').status_code,200)
        self.assertEqual(self.act(app,'accept_offer',offer_id=r.data['id'],reason='Accept').status_code,200)
        self.assertEqual(self.patch(app,state='DRAFT',reason='Rewind').status_code,400)
        self.assertEqual(self.patch(app,state='OFFER_ACCEPTED',notes='Updated note').status_code,200)
        self.assertEqual(self.patch(app,state='ENROLLED',reason='Skip controls').status_code,400)

    def test_deposit_payment_verification_and_final_enrollment(self):
        app=self.accepted();proof=self.evidence(app);self.client.force_authenticate(self.admin)
        self.assertEqual(self.act(app,'deposit',amount='100',currency='usd',reason='Institution requires deposit').status_code,200)
        payload={'enrolled_on':str(timezone.localdate()),'institution_reference':'ENR-01','reason':'Institution confirmed'}
        self.assertEqual(self.act(app,'enroll',**payload).status_code,400)
        for total in ['-1','1000000000000']:
            self.assertEqual(self.act(app,'payment',amount=total,currency='USD',paid_on=str(timezone.localdate()),method='BANK_TRANSFER').status_code,400)
        r=self.act(app,'payment',amount='100',currency='USD',paid_on=str(timezone.localdate()),method='BANK_TRANSFER',reference='TX-1')
        self.assertEqual(r.status_code,200,r.data);payment=Payment.objects.get(application=app)
        self.assertEqual(self.act(app,'payment_status',id=str(payment.pk),status='RECEIVED',reason='Bank receipt').status_code,200)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.act(app,'payment_status',id=str(payment.pk),status='VERIFIED',reason='Reviewed',proof_id=str(proof.pk)).status_code,403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.act(app,'payment_status',id=str(payment.pk),status='VERIFIED',reason='Reviewed').status_code,400)
        self.assertEqual(self.act(app,'payment_status',id=str(payment.pk),status='VERIFIED',reason='Reviewed',proof_id=str(proof.pk)).status_code,200)
        proof.status='RESUBMISSION_REQUIRED';proof.save()
        self.assertEqual(self.act(app,'enroll',**payload).status_code,400)
        proof.status='VERIFIED';proof.save()
        self.assertEqual(self.act(app,'enroll',**payload).status_code,200)
        self.person.refresh_from_db();self.assertEqual(self.person.stage,'STUDENT');self.assertEqual(self.person.student_state,'ENROLLED')
        self.assertEqual(self.act(app,'enroll',**payload).status_code,400)

    def test_enrollment_document_review_and_duplicate_validation(self):
        app=self.accepted();doc=self.evidence(app)
        payload={'kind':'CAS','number':'CAS-1','issued_on':str(timezone.localdate()),'document_id':str(doc.pk)}
        r=self.act(app,'enrollment_document',**payload);self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(self.act(app,'enrollment_document',**payload).status_code,400)
        item=EnrollmentDocument.objects.get(application=app)
        enroll={'enrolled_on':str(timezone.localdate()),'institution_reference':'ENR-2','reason':'Confirmed'}
        self.assertEqual(self.act(app,'enroll',**enroll).status_code,400)
        doc.expires_at=timezone.now()-timedelta(days=1);doc.save()
        self.assertEqual(self.act(app,'verify_enrollment_document',id=item.pk).status_code,400)
        doc.expires_at=None;doc.save()
        self.assertEqual(self.act(app,'verify_enrollment_document',id=item.pk).status_code,200)
        self.assertEqual(self.act(app,'enroll',**enroll).status_code,200)

    def test_visa_checklist_finance_refusal_and_new_attempt(self):
        app=self.accepted();workflow=self.visa_workflow(required_documents=['Passport'],financial_evidence_required=True,predeparture_checklist=['Arrange accommodation'])
        case=self.case(app,workflow)
        self.assertEqual(self.act(app,'visa_state',case_id=str(case.pk),state='APPROVED',reason='Decision').status_code,400)
        self.assertEqual(self.act(app,'visa_state',case_id=str(case.pk),state='READY',reason='Review').status_code,400)
        doc=case.checklist_documents.get().document;doc.status='VERIFIED';doc.version=1;doc.save()
        self.assertEqual(self.act(app,'visa_state',case_id=str(case.pk),state='READY',reason='Review').status_code,400)
        proof=self.evidence(app)
        self.assertEqual(self.act(app,'financial_evidence',case_id=str(case.pk),document_id=str(proof.pk),kind='Bank statement',holder='Student',amount='15000',currency='USD',statement_on=str(timezone.localdate())).status_code,200)
        evidence=case.financial_evidence.get()
        self.assertEqual(self.act(app,'financial_review',case_id=str(case.pk),id=evidence.pk,status='VERIFIED',reason='Statement verified').status_code,200)
        for state in ['READY','SUBMITTED','REFUSED']:
            r=self.act(app,'visa_state',case_id=str(case.pk),state=state,reason='Reviewed milestone');self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(self.act(app,'visa_state',case_id=str(case.pk),state='APPROVED',reason='Overwrite decision').status_code,400)
        retry=self.case(app,workflow);case.refresh_from_db()
        self.assertEqual(retry.attempt_no,2);self.assertEqual(retry.previous_case_id,case.pk);self.assertEqual(case.state,'REFUSED')
        self.assertEqual(PreDeparture.objects.get(application=app).checklist,[{'title':'Arrange accommodation','status':'PENDING'}])
        with self.assertRaises(DatabaseError),transaction.atomic():case.events.update(reason='Tampered')

    def test_visa_information_blockers_appointment_and_approval(self):
        app=self.accepted();case=self.case(app,self.visa_workflow(financial_evidence_required=False))
        due=(timezone.now()+timedelta(days=10)).isoformat()
        self.assertEqual(self.act(app,'visa_information',case_id=str(case.pk),title='Additional evidence',due_at=due,reason='Embassy request').status_code,200)
        case.refresh_from_db();self.assertEqual(case.state,'PREPARING');self.assertTrue(app.person.tasks.filter(title__contains='Additional evidence').exists())
        doc=case.checklist_documents.get().document;doc.status='WAIVED';doc.save()
        self.assertEqual(self.act(app,'visa_blocker',case_id=str(case.pk),type='EVIDENCE',description='Awaiting embassy confirmation').status_code,200)
        self.assertEqual(self.act(app,'visa_state',case_id=str(case.pk),state='READY',reason='Review').status_code,400)
        blocker=Blocker.objects.get(application=app)
        self.client.patch(f'/api/v1/admissions/blockers/{blocker.pk}/',{'reason':'Resolved embassy query'},format='json')
        for attempt in range(2):self.assertEqual(self.act(app,'visa_appointment',case_id=str(case.pk),appointment_at=due,reason='Confirmed booking').status_code,200)
        self.assertEqual(app.deadlines.filter(type='VISA_APPOINTMENT',status='OPEN').count(),1)
        for state in ['READY','SUBMITTED','APPROVED']:
            r=self.act(app,'visa_state',case_id=str(case.pk),state=state,reason='Reviewed milestone');self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(self.act(app,'enroll',enrolled_on=str(timezone.localdate()),institution_reference='ENR-3',reason='Confirmed institution enrollment').status_code,200)

    def test_predeparture_validation_and_saved_details(self):
        app=self.accepted()
        self.assertEqual(self.act(app,'predeparture',checklist=[{'title':'Travel','status':'INVALID'}],reason='Update').status_code,400)
        self.assertEqual(self.act(app,'predeparture',departure_at=(timezone.now()+timedelta(days=2)).isoformat(),arrival_at=timezone.now().isoformat(),reason='Update').status_code,400)
        r=self.act(app,'predeparture',checklist=[{'title':'Flight','status':'DONE'}],accommodation='Student residence',airport_pickup='University transport',flight_number='TEST-01',reason='Travel confirmed')
        self.assertEqual(r.status_code,200,r.data);self.assertEqual(r.data['predeparture'][0]['flight_number'],'TEST-01')

    def test_scope_and_document_staff_cannot_mutate_journey(self):
        app=self.accepted()
        self.assertEqual(self.act(app,'payment_status',id='invalid',status='VERIFIED',reason='Invalid identifier').status_code,403)
        self.assertEqual(self.act(app,'visa_state',case_id='invalid',state='READY',reason='Invalid identifier').status_code,400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.act(app,'predeparture',reason='Unauthorized').status_code,404)
        TeamMember.objects.create(person=self.person,user=self.docs);self.client.force_authenticate(self.docs)
        self.assertEqual(self.client.get(f'/api/v1/progression/applications/{app.pk}/').status_code,200)
        self.assertEqual(self.act(app,'predeparture',reason='Unauthorized').status_code,403)

    def test_workflow_admin_order_version_and_immutability(self):
        path='/api/v1/progression/workflows/'
        data={'country':'Testland','milestones':['PREPARING','READY','SUBMITTED','APPROVED','REFUSED','WITHDRAWN'],'required_documents':[]}
        self.assertEqual(self.client.post(path,data,format='json').status_code,403)
        self.client.force_authenticate(self.admin)
        for version in [1,2]:
            r=self.client.post(path,data,format='json');self.assertEqual(r.status_code,201,r.data);self.assertEqual(r.data['version'],version)
        self.assertEqual(self.client.post(path,{**data,'milestones':list(reversed(data['milestones']))},format='json').status_code,400)
        with self.assertRaises(DatabaseError),transaction.atomic():VisaWorkflow.objects.update(required_documents=['Changed'])

    def test_payment_retry_is_idempotent(self):
        app=self.accepted();self.client.force_authenticate(self.admin);path=f'/api/v1/progression/applications/{app.pk}/'
        data={'action':'payment','amount':'10','currency':'USD','paid_on':str(timezone.localdate()),'method':'CASH'}
        for attempt in range(2):
            r=self.client.post(path,data,format='json',HTTP_IDEMPOTENCY_KEY='phase-three-payment-retry');self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(app.payments.count(),1)

    def test_configured_enrollment_and_visa_requirements_cannot_be_skipped(self):
        from admissions.models import WorkflowTemplate
        self.workflow=WorkflowTemplate.objects.create(destination='Testland',version=2,milestones=self.workflow.milestones,enrollment_requirements=['CAS'],visa_requirements=['Visa approval'],created_by=self.admin)
        app=self.accepted();payload={'enrolled_on':str(timezone.localdate()),'institution_reference':'ENR-required','reason':'Institution confirmed'}
        self.assertEqual(self.act(app,'enroll',**payload).status_code,400)
        doc=self.evidence(app)
        r=self.act(app,'enrollment_document',kind='CAS',number='CAS-required',issued_on=str(timezone.localdate()),document_id=str(doc.pk));self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(self.act(app,'verify_enrollment_document',id=app.enrollment_documents.get().pk).status_code,200)
        self.assertEqual(self.act(app,'enroll',**payload).status_code,400)
        case=self.case(app,self.visa_workflow(financial_evidence_required=False))
        for state in ['READY','SUBMITTED','APPROVED']:
            self.assertEqual(self.act(app,'visa_state',case_id=str(case.pk),state=state,reason='Reviewed').status_code,200)
        doc.status='RESUBMISSION_REQUIRED';doc.save()
        self.assertEqual(self.act(app,'enroll',**payload).status_code,400)
        doc.status='VERIFIED';doc.save()
        self.assertEqual(self.act(app,'enroll',**payload).status_code,200)
