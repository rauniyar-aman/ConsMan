import uuid
from datetime import time
from django.conf import settings
from django.db import models
from django.utils import timezone

def default_working_days():
    return [0, 1, 2, 3, 4, 6]

class Branch(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)

class StaffProfile(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Super Admin'
        MANAGER = 'MANAGER', 'Branch Manager'
        COUNSELOR = 'COUNSELOR', 'Counselor'
        DOCS = 'DOCS', 'Documentation Staff'
        FINANCE = 'FINANCE', 'Finance Staff'
        MANAGEMENT = 'MANAGEMENT', 'Management'
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='staff')
    role = models.CharField(max_length=20, choices=Role.choices)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    availability = models.CharField(max_length=20, default='ACTIVE', choices=[('ACTIVE','Active'),('ON_LEAVE','On leave'),('INACTIVE','Inactive')])
    leave_until = models.DateField(null=True, blank=True)
    max_open_leads = models.PositiveIntegerField(default=100)

class Source(models.Model):
    name = models.CharField(max_length=100, unique=True)
    active = models.BooleanField(default=True)

class Campaign(models.Model):
    name = models.CharField(max_length=120)
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)

class SourceDetail(models.Model):
    name = models.CharField(max_length=120)
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)

class Tag(models.Model):
    name = models.CharField(max_length=60, unique=True)
    active = models.BooleanField(default=True)

class LostReason(models.Model):
    name = models.CharField(max_length=100, unique=True)
    active = models.BooleanField(default=True)
    note_required = models.BooleanField(default=False)

class ReferenceCounter(models.Model):
    year = models.PositiveIntegerField(unique=True)
    value = models.PositiveIntegerField(default=0)

class Person(models.Model):
    class Status(models.TextChoices):
        NEW = 'NEW', 'New'
        CONTACTED = 'CONTACTED', 'Contacted'
        COUNSELING = 'COUNSELING', 'Counseling'
        INTERESTED = 'INTERESTED', 'Interested'
        DOCUMENT_COLLECTION = 'DOCUMENT_COLLECTION', 'Document collection'
        APPLICATION_READY = 'APPLICATION_READY', 'Application ready'
        CONVERTED = 'CONVERTED', 'Converted'
        LOST = 'LOST', 'Lost'
        ON_HOLD = 'ON_HOLD', 'On hold'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ref = models.CharField(max_length=20, unique=True, editable=False)
    full_name = models.CharField(max_length=160)
    normalized_name = models.CharField(max_length=160,blank=True,db_index=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='people', null=True, blank=True)
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    stage = models.CharField(max_length=10, default='LEAD', choices=[('LEAD', 'Lead'), ('STUDENT', 'Student')])
    lead_status = models.CharField(max_length=30, choices=Status.choices, default=Status.NEW)
    temperature = models.CharField(max_length=4, choices=[('HOT','Hot'),('WARM','Warm'),('COLD','Cold')], default='WARM')
    student_state = models.CharField(max_length=20, blank=True)
    preferred_country = models.CharField(max_length=80, blank=True)
    preferred_course = models.CharField(max_length=160, blank=True)
    address = models.CharField(max_length=255, blank=True)
    next_action_due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    converted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    dob = models.DateField(null=True, blank=True)
    guardian_name = models.CharField(max_length=160, blank=True)
    guardian_contact = models.CharField(max_length=40, blank=True)
    preferred_countries = models.JSONField(default=list)
    study_levels = models.JSONField(default=list)
    preferred_intake = models.CharField(max_length=80, blank=True)
    preferred_university = models.CharField(max_length=160, blank=True)
    highest_education = models.CharField(max_length=100, blank=True)
    work_experience = models.TextField(blank=True)
    previous_visa_refusal = models.CharField(max_length=7, default='UNKNOWN', choices=[('YES','Yes'),('NO','No'),('UNKNOWN','Unknown')])
    best_contact_method = models.CharField(max_length=20, default='CALL')
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, null=True, blank=True)
    source_detail = models.ForeignKey(SourceDetail, on_delete=models.PROTECT, null=True, blank=True)
    tags = models.ManyToManyField(Tag, blank=True)
    lost_reason = models.ForeignKey(LostReason, on_delete=models.PROTECT, null=True, blank=True)
    hold_until = models.DateTimeField(null=True, blank=True)
    merged_into = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True)
    next_action_type = models.CharField(max_length=20, blank=True)
    next_action_owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    import_batch_id = models.UUIDField(null=True, blank=True)
    import_row_number = models.PositiveIntegerField(null=True, blank=True)
    source_file = models.CharField(max_length=255, blank=True)
    def save(self,*args,**kwargs):
        from .services import normalize_name
        self.normalized_name=normalize_name(self.full_name)
        if kwargs.get('update_fields') and 'full_name' in kwargs['update_fields']:kwargs['update_fields']=list(set(kwargs['update_fields'])|{'normalized_name'})
        return super().save(*args,**kwargs)
    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['branch', 'owner', 'lead_status'])]

class ContactMethod(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='contacts')
    type = models.CharField(max_length=10)
    raw_value = models.CharField(max_length=254)
    normalized_value = models.CharField(max_length=254, db_index=True)
    is_shared = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_via = models.CharField(max_length=30, default='NONE')
    is_primary = models.BooleanField(default=False)
    shared_with_note = models.CharField(max_length=200, blank=True)

class ConsentRecord(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT)
    wording_version = models.CharField(max_length=30, default='staff-v1')
    given_at = models.DateTimeField(auto_now_add=True)
    method = models.CharField(max_length=20, default='STAFF')
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    channels = models.JSONField(default=list)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict)

class Activity(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='activities')
    type = models.CharField(max_length=30, default='NOTE')
    subject = models.CharField(max_length=200)
    notes = models.TextField(blank=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    performed_at = models.DateTimeField(auto_now_add=True)
    corrects = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='corrections')
    channel = models.CharField(max_length=20, default='SYSTEM')
    direction = models.CharField(max_length=10, default='INTERNAL')
    outcome = models.CharField(max_length=200, blank=True)
    class Meta:
        ordering = ['-performed_at']

class FollowUp(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='followups')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    subject = models.CharField(max_length=200)
    method = models.CharField(max_length=20, default='CALL', choices=[('CALL','Call'),('WHATSAPP','WhatsApp'),('EMAIL','Email'),('MEETING','Meeting')])
    due_at = models.DateTimeField(db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, default='OPEN', choices=[('OPEN','Open'),('IN_PROGRESS','In progress'),('COMPLETED','Completed'),('CANCELLED','Cancelled')])
    created_at = models.DateTimeField(default=timezone.now,editable=False)
    notes = models.TextField(blank=True)
    class Meta:
        ordering = ['due_at']

class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    action = models.CharField(max_length=60)
    object_id = models.CharField(max_length=60)
    old = models.JSONField(default=dict)
    new = models.JSONField(default=dict)
    request_id = models.CharField(max_length=36)
    at = models.DateTimeField(auto_now_add=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, null=True)

class TeamMember(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='team_members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    role_on_case = models.CharField(max_length=30, default='DOCUMENTATION')
    active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['person','user'], name='unique_team_member')]

class EducationRecord(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='education')
    level = models.CharField(max_length=80)
    institute = models.CharField(max_length=160)
    degree_stream = models.CharField(max_length=120, blank=True)
    grade_or_percent = models.CharField(max_length=40, blank=True)
    passed_year = models.PositiveIntegerField(null=True, blank=True)

class TestScore(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='test_scores')
    test = models.CharField(max_length=30)
    score = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=20, default='PLANNED')
    test_date = models.DateField(null=True, blank=True)

class Task(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='tasks', null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    priority = models.CharField(max_length=10, default='NORMAL')
    due_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=20, default='OPEN')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    type = models.CharField(max_length=40)
    title = models.CharField(max_length=160)
    message = models.TextField(blank=True)
    person = models.ForeignKey(Person, on_delete=models.PROTECT, null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    dedupe_key = models.CharField(max_length=160, unique=True, null=True, blank=True)
    class Meta:
        ordering = ['-created_at']

class OwnershipHistory(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT)
    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True)
    to_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True)
    reason = models.CharField(max_length=500)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    request_id = models.CharField(max_length=36)

class AccessRequest(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    type = models.CharField(max_length=10, default='ACCESS')
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=10, default='PENDING')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True)
    decided_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AccessGrant(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='access_grants')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['person','user'], name='unique_access_grant')]

class DuplicateCandidate(models.Model):
    person_a = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='+')
    person_b = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='+')
    confidence = models.CharField(max_length=10)
    signals = models.JSONField(default=list)
    status = models.CharField(max_length=20, default='OPEN')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['person_a','person_b'], name='unique_duplicate_pair')]

class MergeRecord(models.Model):
    survivor = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='+')
    merged = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='+')
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    performed_at = models.DateTimeField(auto_now_add=True)
    field_choices = models.JSONField(default=dict)
    moved_objects = models.JSONField(default=dict)
    reversible_until = models.DateTimeField()
    reversed_at = models.DateTimeField(null=True)

class BusinessCalendar(models.Model):
    branch = models.OneToOneField(Branch, on_delete=models.PROTECT)
    timezone = models.CharField(max_length=60, default='Asia/Kathmandu')
    working_days = models.JSONField(default=default_working_days)
    open_time = models.TimeField(default=time(10,0))
    close_time = models.TimeField(default=time(17,0))
    holidays = models.JSONField(default=list)

class SlaRule(models.Model):
    name = models.CharField(max_length=100, unique=True)
    trigger = models.CharField(max_length=30, unique=True)
    target_business_hours = models.PositiveIntegerField()
    active = models.BooleanField(default=True)

class SlaTimer(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='sla_timers', null=True)
    access_request = models.ForeignKey(AccessRequest, on_delete=models.PROTECT, null=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    rule = models.ForeignKey(SlaRule, on_delete=models.PROTECT)
    started_at = models.DateTimeField()
    due_at = models.DateTimeField(db_index=True)
    satisfied_at = models.DateTimeField(null=True)
    breached_at = models.DateTimeField(null=True)

class AssignmentRule(models.Model):
    branch = models.OneToOneField(Branch, on_delete=models.PROTECT)
    mode = models.CharField(max_length=20, default='MANAGER_QUEUE')
    last_owner_id = models.PositiveIntegerField(default=0)

class IdempotencyRecord(models.Model):
    key = models.CharField(max_length=160)
    scope = models.CharField(max_length=200)
    request_hash = models.CharField(max_length=64)
    response = models.JSONField(null=True)
    status_code = models.PositiveIntegerField(default=200)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['key','scope'], name='unique_idempotency_key_scope')]

class MfaDevice(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    encrypted_secret = models.TextField()
    confirmed = models.BooleanField(default=False)
    last_counter = models.BigIntegerField(default=-1)

class SystemPolicy(models.Model):
    key = models.CharField(max_length=80, unique=True)
    value = models.JSONField()

class Document(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name='documents')
    type = models.CharField(max_length=60)
    storage_key = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, default='REQUESTED')
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
