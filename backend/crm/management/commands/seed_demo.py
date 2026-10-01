import os
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from crm.models import Activity, Branch, ConsentRecord, ContactMethod, FollowUp, Person, Source, StaffProfile
from crm.services import next_reference, refresh_next_action

class Command(BaseCommand):
    help = 'Create fictional development data. Never run against production.'
    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Demo seeding is allowed only in DEBUG mode.')
        password = os.getenv('DEMO_PASSWORD')
        if not password or len(password) < 12:
            raise CommandError('Set DEMO_PASSWORD to a local password of at least 12 characters.')
        branch, _ = Branch.objects.get_or_create(code='KTM', defaults={'name':'Kathmandu'})
        users=[]
        for username,name,role in [('manager','Samira Karki','MANAGER'),('counselor','Anish Shrestha','COUNSELOR'),('counselor2','Priya Thapa','COUNSELOR'),('admin','Demo Administrator','ADMIN'),('docs','Demo Documentation','DOCS'),('management','Demo Management','MANAGEMENT'),('finance','Demo Finance','FINANCE')]:
            user, created = User.objects.get_or_create(username=username, defaults={'first_name':name.split()[0],'last_name':name.split()[1]})
            if created:
                user.set_password(password); user.save()
            StaffProfile.objects.get_or_create(user=user, defaults={'branch':branch,'role':role})
            users.append(user)
        sources=[Source.objects.get_or_create(name=name)[0] for name in ['Walk-in','Website','Referral','Education fair','Social media']]
        names=['Aarav Sharma','Sushmita Rai','Niraj Adhikari','Prakriti Gurung','Bishal Tamang','Sneha Koirala','Rohan Poudel','Anusha Bhandari','सुमन श्रेष्ठ','Kritika Basnet','Ashish Karki','Diya Thapa']
        countries=['Australia','United Kingdom','Canada','United States']
        statuses=['NEW','CONTACTED','COUNSELING','INTERESTED','DOCUMENT_COLLECTION','APPLICATION_READY']
        for i,name in enumerate(names):
            if Person.objects.filter(full_name=name, created_by=users[0]).exists():
                continue
            person=Person.objects.create(ref=next_reference(),full_name=name,owner=users[1+i%2],branch=branch,source=sources[i%5],temperature=['HOT','WARM','COLD'][i%3],preferred_country=countries[i%4],preferred_course=['Business Management','Computer Science','Nursing'][i%3],lead_status=statuses[i%6],created_by=users[0])
            ContactMethod.objects.create(person=person,type='PHONE',raw_value=f'98012345{i:02}',normalized_value=f'+97798012345{i:02}')
            ContactMethod.objects.create(person=person,type='EMAIL',raw_value=f'demo.student{i}@example.com',normalized_value=f'demo.student{i}@example.com')
            ConsentRecord.objects.create(person=person,recorded_by=users[0])
            Activity.objects.create(person=person,subject='Lead added — fictional demo record',type='CREATED',performed_by=users[0])
            if i<8:
                FollowUp.objects.create(person=person,owner=person.owner,subject=['Discuss study options','Check preferred intake','Follow up on counseling'][i%3],due_at=timezone.now()+timedelta(hours=i-3),method=['CALL','WHATSAPP','EMAIL'][i%3])
                refresh_next_action(person)
        self.stdout.write(self.style.SUCCESS('Demo workspace ready. Usernames: manager, counselor, counselor2, admin, docs, management, finance. Use your DEMO_PASSWORD; admin/finance enroll MFA on first sign-in.'))
