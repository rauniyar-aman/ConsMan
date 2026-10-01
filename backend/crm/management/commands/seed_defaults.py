from django.core.management.base import BaseCommand
from django.db import transaction
from crm.models import Branch,Source,Campaign,Tag,LostReason,BusinessCalendar,SlaRule,AssignmentRule,SystemPolicy
from intake.services import DEFAULTS

class Command(BaseCommand):
    help='Idempotently seed Phase 1 configuration and controlled master lists.'
    @transaction.atomic
    def handle(self,*args,**options):
        for name in ['Walk-in','Website','Referral','Education fair','Social media']:Source.objects.get_or_create(name=name)
        source=Source.objects.get(name='Walk-in')
        for branch in Branch.objects.all():
            BusinessCalendar.objects.get_or_create(branch=branch)
            AssignmentRule.objects.get_or_create(branch=branch)
            Campaign.objects.get_or_create(name=f'Walk-in — {branch.name}',source=source)
        for name in ['IELTS preparation','Priority','Education fair']:Tag.objects.get_or_create(name=name)
        reasons=['Cost / financial constraints','Changed destination','Changed university','Visa refusal concern','Academic eligibility / profile mismatch','Family decision','Not responding / not reachable','Not interested','Chose another consultancy','Applied directly','Delayed study plan','Personal reason','Duplicate / invalid record','Other']
        for name in reasons:LostReason.objects.get_or_create(name=name,defaults={'note_required':name=='Other'})
        for trigger,name,hours in [('FIRST_CONTACT','New lead first contact',24),('HOT_CONTACT','Hot lead first contact',4),('ASSIGNMENT','Verified QR lead assignment',2),('ACCESS','Access-request response',8)]:SlaRule.objects.get_or_create(trigger=trigger,defaults={'name':name,'target_business_hours':hours})
        for key,value in {**DEFAULTS,'stale_days':30,'closed_lead_retention_months':36}.items():SystemPolicy.objects.get_or_create(key=key,defaults={'value':value})
        self.stdout.write(self.style.SUCCESS('Phase 1 defaults seeded. Existing configured values preserved.'))
