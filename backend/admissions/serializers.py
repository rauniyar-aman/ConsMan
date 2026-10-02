from rest_framework import serializers
from .models import *
from .services import PHASE_TWO_STATES


class UniversitySerializer(serializers.ModelSerializer):
    class Meta:model=University;fields='__all__'

class CampusSerializer(serializers.ModelSerializer):
    class Meta:model=Campus;fields='__all__'

class CourseSerializer(serializers.ModelSerializer):
    class Meta:model=Course;fields='__all__'

class IntakeSerializer(serializers.ModelSerializer):
    class Meta:model=Intake;fields='__all__'

class OfferingSerializer(serializers.ModelSerializer):
    class Meta:model=CourseOffering;fields='__all__'
    def validate(self,data):
        course=data.get('course',getattr(self.instance,'course',None));campus=data.get('campus',getattr(self.instance,'campus',None))
        if data.get('fee',0)<0:raise serializers.ValidationError('Course fee cannot be negative.')
        if course and campus and course.university_id!=campus.university_id:raise serializers.ValidationError('Course and campus must belong to the same university.')
        for field in ['minimum_academic_percent','minimum_english_score']:
            if data.get(field) is not None and data[field]<0:raise serializers.ValidationError({field:'Use a nonnegative value.'})
        if data.get('minimum_academic_percent') is not None and data['minimum_academic_percent']>100:raise serializers.ValidationError('Academic percentage must not exceed 100.')
        if data.get('minimum_english_score') is not None and not data.get('english_test',getattr(self.instance,'english_test','')):raise serializers.ValidationError('Select an English test for its score requirement.')
        if 'currency' in data and (len(data['currency'])!=3 or not data['currency'].isalpha()):raise serializers.ValidationError('Use a three-letter currency code.')
        if 'currency' in data:data['currency']=data['currency'].upper()
        return data

class ScholarshipSerializer(serializers.ModelSerializer):
    class Meta:model=Scholarship;fields='__all__'
    def validate(self,data):
        if data.get('amount',0)<0:raise serializers.ValidationError('Scholarship amount cannot be negative.')
        score=data.get('minimum_academic_percent')
        if score is not None and not 0<=score<=100:raise serializers.ValidationError('Academic percentage must be between 0 and 100.')
        return data

class WorkflowSerializer(serializers.ModelSerializer):
    class Meta:model=WorkflowTemplate;exclude=['created_by'];read_only_fields=['version','created_at']
    def validate(self,data):
        milestones=data.get('milestones',PHASE_TWO_STATES)
        if not isinstance(milestones,list) or not 2<=len(milestones)<=20 or any(not isinstance(x,str) for x in milestones) or len(set(milestones))!=len(milestones) or milestones[0]!='DRAFT' or milestones[-1]!='OFFER_RECEIVED':raise serializers.ValidationError('Use unique milestones beginning with DRAFT and ending with OFFER_RECEIVED.')
        if any(not isinstance(x,str) or not x or len(x)>30 or x in ['OFFER_ACCEPTED','ENROLLED'] for x in milestones):raise serializers.ValidationError('Invalid or later-phase milestone.')
        documents=data.get('required_documents',[])
        if not isinstance(documents,list) or len(documents)>50:raise serializers.ValidationError('Use at most 50 document requirements.')
        seen=set()
        for d in documents:
            if not isinstance(d,dict) or not isinstance(d.get('type'),str) or not d['type'] or len(d['type'])>80 or not isinstance(d.get('title'),str) or not d['title'] or len(d['title'])>160 or type(d.get('required',True))!=bool or d['type'] in seen:raise serializers.ValidationError('Each document needs a unique type, title and required flag.')
            seen.add(d['type'])
        tasks=data.get('task_templates',{})
        if not isinstance(tasks,dict) or any(k not in milestones or not isinstance(v,list) or len(v)>20 or any(not isinstance(x,str) or not x.strip() or len(x)>200 for x in v) for k,v in tasks.items()):raise serializers.ValidationError('Task templates must map milestones to task titles.')
        for field in ['offer_conditions','enrollment_requirements','visa_requirements']:
            if field in data and (not isinstance(data[field],list) or any(not isinstance(x,str) or len(x)>500 for x in data[field])):raise serializers.ValidationError({field:'Use a list of descriptions.'})
        data['milestones']=milestones
        return data

CATALOG={'universities':UniversitySerializer,'campuses':CampusSerializer,'courses':CourseSerializer,'intakes':IntakeSerializer,'offerings':OfferingSerializer,'scholarships':ScholarshipSerializer,'workflows':WorkflowSerializer}
