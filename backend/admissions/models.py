import uuid
from django.conf import settings
from django.db import models


class University(models.Model):
    name=models.CharField(max_length=160)
    country=models.CharField(max_length=80)
    website=models.URLField(blank=True)
    active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['name','country'],name='unique_university_country')]


class Campus(models.Model):
    university=models.ForeignKey(University,on_delete=models.PROTECT,related_name='campuses')
    name=models.CharField(max_length=160)
    city=models.CharField(max_length=100,blank=True)
    address=models.CharField(max_length=255,blank=True)
    active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['university','name'],name='unique_university_campus')]


class Course(models.Model):
    university=models.ForeignKey(University,on_delete=models.PROTECT,related_name='courses')
    name=models.CharField(max_length=160)
    level=models.CharField(max_length=80)
    duration_months=models.PositiveIntegerField(default=12)
    requirements=models.TextField(blank=True)
    english_requirements=models.TextField(blank=True)
    active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['university','name','level'],name='unique_university_course_level')]


class Intake(models.Model):
    name=models.CharField(max_length=100)
    start_date=models.DateField()
    application_deadline=models.DateTimeField(null=True,blank=True)
    active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['name','start_date'],name='unique_intake_start')]


class CourseOffering(models.Model):
    course=models.ForeignKey(Course,on_delete=models.PROTECT)
    campus=models.ForeignKey(Campus,on_delete=models.PROTECT)
    intake=models.ForeignKey(Intake,on_delete=models.PROTECT)
    fee=models.DecimalField(max_digits=12,decimal_places=2,default=0)
    currency=models.CharField(max_length=3,default='USD')
    requirements=models.TextField(blank=True)
    english_test=models.CharField(max_length=20,blank=True)
    minimum_english_score=models.DecimalField(max_digits=5,decimal_places=2,null=True,blank=True)
    minimum_academic_percent=models.DecimalField(max_digits=5,decimal_places=2,null=True,blank=True)
    availability=models.CharField(max_length=20,default='OPEN',choices=[('OPEN','Open'),('LIMITED','Limited'),('CLOSED','Closed')])
    active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['course','campus','intake'],name='unique_course_campus_intake'),models.CheckConstraint(condition=models.Q(fee__gte=0),name='offering_fee_nonnegative')]


class Scholarship(models.Model):
    offering=models.ForeignKey(CourseOffering,on_delete=models.PROTECT,related_name='scholarships')
    name=models.CharField(max_length=160)
    amount=models.DecimalField(max_digits=12,decimal_places=2,default=0)
    eligibility=models.TextField(blank=True)
    minimum_academic_percent=models.DecimalField(max_digits=5,decimal_places=2,null=True,blank=True)
    deadline=models.DateTimeField(null=True,blank=True)
    active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(amount__gte=0),name='scholarship_amount_nonnegative')]


class WorkflowTemplate(models.Model):
    destination=models.CharField(max_length=80)
    version=models.PositiveIntegerField()
    milestones=models.JSONField(default=list)
    required_documents=models.JSONField(default=list)
    offer_conditions=models.JSONField(default=list)
    enrollment_requirements=models.JSONField(default=list)
    visa_requirements=models.JSONField(default=list)
    task_templates=models.JSONField(default=dict)
    active=models.BooleanField(default=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['destination','version'],name='unique_workflow_destination_version')]


class AdmissionCounter(models.Model):
    year=models.PositiveIntegerField(unique=True)
    value=models.PositiveIntegerField(default=0)


class Application(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    ref=models.CharField(max_length=24,unique=True,editable=False)
    person=models.ForeignKey('crm.Person',on_delete=models.PROTECT,related_name='applications')
    course_offering=models.ForeignKey(CourseOffering,on_delete=models.PROTECT)
    workflow_template=models.ForeignKey(WorkflowTemplate,on_delete=models.PROTECT)
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='admission_applications')
    state=models.CharField(max_length=30,default='DRAFT')
    notes=models.TextField(blank=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        indexes=[models.Index(fields=['state','created_at'])]
    @property
    def branch(self):return self.person.branch


class ApplicationEvent(models.Model):
    application=models.ForeignKey(Application,on_delete=models.PROTECT,related_name='events')
    type=models.CharField(max_length=40)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    old=models.JSONField(default=dict)
    new=models.JSONField(default=dict)
    reason=models.TextField(blank=True)
    created_at=models.DateTimeField(auto_now_add=True)


class Document(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    person=models.ForeignKey('crm.Person',on_delete=models.PROTECT,related_name='admission_documents')
    application=models.ForeignKey(Application,on_delete=models.PROTECT,related_name='documents',null=True,blank=True)
    type=models.CharField(max_length=80)
    title=models.CharField(max_length=160)
    required=models.BooleanField(default=True)
    status=models.CharField(max_length=30,default='REQUESTED')
    expires_at=models.DateTimeField(null=True,blank=True)
    verified_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    verified_at=models.DateTimeField(null=True,blank=True)
    version=models.PositiveIntegerField(default=0)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['application','type'],name='unique_application_document_type')]


class DocumentVersion(models.Model):
    document=models.ForeignKey(Document,on_delete=models.PROTECT,related_name='versions')
    version=models.PositiveIntegerField()
    filename=models.CharField(max_length=160)
    mime=models.CharField(max_length=80)
    checksum=models.CharField(max_length=64)
    data=models.BinaryField()
    uploaded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['document','version'],name='unique_document_version')]


class Deadline(models.Model):
    person=models.ForeignKey('crm.Person',on_delete=models.PROTECT,related_name='admission_deadlines')
    application=models.ForeignKey(Application,on_delete=models.PROTECT,related_name='deadlines',null=True,blank=True)
    document=models.ForeignKey(Document,on_delete=models.PROTECT,null=True,blank=True)
    type=models.CharField(max_length=30)
    title=models.CharField(max_length=160)
    due_at=models.DateTimeField(db_index=True)
    source=models.CharField(max_length=40,default='MANUAL')
    status=models.CharField(max_length=20,default='OPEN')
    completed_at=models.DateTimeField(null=True,blank=True)


class Blocker(models.Model):
    person=models.ForeignKey('crm.Person',on_delete=models.PROTECT,related_name='admission_blockers')
    application=models.ForeignKey(Application,on_delete=models.PROTECT,related_name='blockers',null=True,blank=True)
    type=models.CharField(max_length=60)
    description=models.TextField()
    assigned_to=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at=models.DateTimeField(auto_now_add=True)
    resolved_at=models.DateTimeField(null=True,blank=True)
    resolution=models.TextField(blank=True)


class ApplicationTask(models.Model):
    application=models.ForeignKey(Application,on_delete=models.PROTECT,related_name='generated_tasks')
    task=models.OneToOneField('crm.Task',on_delete=models.PROTECT)
    milestone=models.CharField(max_length=30)


class OfferReceipt(models.Model):
    application=models.ForeignKey(Application,on_delete=models.PROTECT,related_name='offers')
    type=models.CharField(max_length=20,default='CONDITIONAL')
    conditions=models.JSONField(default=list)
    expires_at=models.DateTimeField(null=True,blank=True)
    received_at=models.DateTimeField()
    status=models.CharField(max_length=20,default='RECEIVED')
    reference=models.CharField(max_length=120,blank=True)
    document=models.ForeignKey(Document,on_delete=models.PROTECT,null=True,blank=True)


class StudentPreference(models.Model):
    person=models.OneToOneField('crm.Person',on_delete=models.PROTECT,related_name='admission_preference')
    countries=models.JSONField(default=list)
    levels=models.JSONField(default=list)
    courses=models.ManyToManyField(Course,blank=True)
    intakes=models.ManyToManyField(Intake,blank=True)
    maximum_fee=models.DecimalField(max_digits=12,decimal_places=2,null=True,blank=True)
    currency=models.CharField(max_length=3,default='USD')
    academic_percent=models.DecimalField(max_digits=5,decimal_places=2,null=True,blank=True)
    notes=models.TextField(blank=True)
