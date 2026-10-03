from rest_framework import serializers
from .models import Activity, FollowUp, Person

class PersonSerializer(serializers.ModelSerializer):
    phone = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()
    owner_name = serializers.CharField(source='owner.get_full_name', default='Unassigned')
    branch_name = serializers.CharField(source='branch.name')
    source_name = serializers.CharField(source='source.name')
    lost_reason_name=serializers.CharField(source='lost_reason.name',default='',read_only=True)
    class Meta:
        model = Person
        fields = ['id','ref','full_name','phone','email','branch','owner','source','campaign','source_detail','tags','branch_name','owner_name','source_name','stage','lead_status','temperature','student_state','preferred_country','preferred_countries','study_levels','preferred_course','preferred_intake','preferred_university','address','dob','guardian_name','guardian_contact','highest_education','work_experience','previous_visa_refusal','best_contact_method','lost_reason','lost_reason_name','hold_until','next_action_due_at','next_action_type','next_action_owner','created_at','updated_at','import_batch_id','import_row_number','source_file']
    def get_phone(self, obj) -> str:
        return next((c.normalized_value for c in obj.contacts.all() if c.type == 'PHONE'), '')
    def get_email(self, obj) -> str:
        return next((c.normalized_value for c in obj.contacts.all() if c.type == 'EMAIL'), '')

class CreatePersonSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=160)
    phone = serializers.CharField(max_length=40)
    email = serializers.EmailField(required=False, allow_blank=True)
    owner_id = serializers.IntegerField()
    branch_id = serializers.IntegerField()
    source_id = serializers.IntegerField()
    temperature = serializers.ChoiceField(choices=['HOT','WARM','COLD'], default='WARM')
    preferred_country = serializers.CharField(max_length=80, required=False, allow_blank=True)
    preferred_course = serializers.CharField(max_length=160, required=False, allow_blank=True)
    address = serializers.CharField(max_length=255, required=False, allow_blank=True)
    consent = serializers.BooleanField()
    duplicate_reason = serializers.CharField(max_length=500, required=False, allow_blank=True)
    dob = serializers.DateField(required=False,allow_null=True)
    guardian_name = serializers.CharField(max_length=160,required=False,allow_blank=True)
    guardian_contact = serializers.CharField(max_length=40,required=False,allow_blank=True)
    preferred_countries = serializers.ListField(child=serializers.CharField(max_length=80),max_length=20,required=False)
    study_levels = serializers.ListField(child=serializers.CharField(max_length=80),max_length=20,required=False)
    preferred_intake = serializers.CharField(max_length=80,required=False,allow_blank=True)
    preferred_university = serializers.CharField(max_length=160,required=False,allow_blank=True)
    highest_education = serializers.CharField(max_length=100,required=False,allow_blank=True)
    work_experience = serializers.CharField(max_length=3000,required=False,allow_blank=True)
    previous_visa_refusal = serializers.ChoiceField(choices=[('YES','Yes'),('NO','No'),('UNKNOWN','Unknown')],required=False)
    best_contact_method = serializers.ChoiceField(choices=['CALL','WHATSAPP','EMAIL','MEETING'],required=False)
    def validate_consent(self, value):
        if not value:
            raise serializers.ValidationError('Confirm consent before creating a person.')
        return value

class ActivitySerializer(serializers.ModelSerializer):
    actor = serializers.CharField(source='performed_by.get_full_name', read_only=True,default='System')
    type = serializers.ChoiceField(choices=['NOTE','CALL','WHATSAPP','SMS','MESSENGER','EMAIL','MEETING'])
    channel=serializers.ChoiceField(choices=['SYSTEM','CALL','WHATSAPP','SMS','MESSENGER','EMAIL','MEETING'],required=False)
    direction=serializers.ChoiceField(choices=['INTERNAL','INBOUND','OUTBOUND'],required=False)
    notes=serializers.CharField(max_length=10000,allow_blank=True,required=False)
    class Meta:
        model = Activity
        fields = ['id','type','channel','direction','subject','notes','outcome','corrects','actor','performed_at']
        read_only_fields = ['id','actor','performed_at','corrects']

class FollowUpSerializer(serializers.ModelSerializer):
    can_complete = serializers.SerializerMethodField()
    def get_can_complete(self,obj):
        return not obj.completed_at and obj.status in ['OPEN','IN_PROGRESS'] and obj.person_id in self.context.get('work_person_ids',set())
    completed_by_name = serializers.SerializerMethodField()
    def get_completed_by_name(self,obj):
        return (obj.completed_by.get_full_name() or obj.completed_by.username) if obj.completed_by_id else ''
    owner_name = serializers.CharField(source='owner.get_full_name', read_only=True, default='Unassigned')
    person_name = serializers.CharField(source='person.full_name', read_only=True)
    person_ref = serializers.CharField(source='person.ref', read_only=True)
    person_id = serializers.UUIDField(read_only=True)
    class Meta:
        model = FollowUp
        fields = ['id','person_id','person_name','person_ref','subject','method','due_at','status','notes','owner_name','created_at','completed_at','completed_by_name','can_complete','outcome']
        read_only_fields = ['id','created_at','completed_at','outcome','status']

class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)

class OutcomeSerializer(serializers.Serializer):
    outcome = serializers.CharField(max_length=200)

class SessionUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    role = serializers.CharField()
    branch = serializers.CharField()
    branch_id = serializers.IntegerField()
    permissions = serializers.DictField(child=serializers.CharField())

class SessionSerializer(serializers.Serializer):
    user = SessionUserSerializer(allow_null=True)
    csrf_token = serializers.CharField()

class LoginResultSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    csrf_token = serializers.CharField()

class PersonListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = PersonSerializer(many=True)

class PersonDetailSerializer(serializers.Serializer):
    person = PersonSerializer()
    activities = ActivitySerializer(many=True)
    followups = FollowUpSerializer(many=True)

class MasterItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()

class OwnerSerializer(MasterItemSerializer):
    branch_id = serializers.IntegerField()

class MastersSerializer(serializers.Serializer):
    branches = MasterItemSerializer(many=True)
    sources = MasterItemSerializer(many=True)
    owners = OwnerSerializer(many=True)

class PipelineSerializer(serializers.Serializer):
    status = serializers.CharField()
    count = serializers.IntegerField()

class DashboardSerializer(serializers.Serializer):
    people = serializers.IntegerField()
    active_leads = serializers.IntegerField()
    hot_leads = serializers.IntegerField()
    overdue = serializers.IntegerField()
    due_today = serializers.IntegerField()
    no_action = serializers.IntegerField()
    students = serializers.IntegerField()
    pipeline = PipelineSerializer(many=True)
    recent = PersonSerializer(many=True)
