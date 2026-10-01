import statistics
import time
import uuid
from django.contrib.auth.models import User
from django.test import TestCase,tag
from rest_framework.test import APIClient
from .models import Branch,Source,StaffProfile,Person,ContactMethod

@tag('performance')
class PhaseOneReadPerformance(TestCase):
    @classmethod
    def setUpTestData(cls):
        branch=Branch.objects.create(name='Performance fixture',code='PERF')
        source=Source.objects.create(name='Performance fixture')
        cls.user=User.objects.create(username='performance-manager')
        StaffProfile.objects.create(user=cls.user,role='MANAGER',branch=branch)
        people=[Person(id=uuid.uuid4(),ref=f'BE-2026-{i:06d}',full_name=f'Performance Person {i:05d}',branch=branch,source=source,owner=cls.user,preferred_country='Australia') for i in range(20000)]
        Person.objects.bulk_create(people,batch_size=1000)
        ContactMethod.objects.bulk_create([ContactMethod(person=p,type='PHONE',raw_value=f'+97798{i:08d}',normalized_value=f'+97798{i:08d}') for i,p in enumerate(people)],batch_size=1000)

    def test_search_and_filters_at_twenty_thousand_people(self):
        client=APIClient();client.force_authenticate(self.user)
        samples=[]
        for i in range(30):
            query=f'?search=Person+{i*613:05d}' if i%2 else '?preferred_country=Australia&lead_status=NEW&temperature=WARM'
            started=time.perf_counter();response=client.get('/api/v1/people/'+query);samples.append(time.perf_counter()-started)
            self.assertEqual(response.status_code,200)
        p95=statistics.quantiles(samples,n=20)[18]
        print(f'20,000-person local API read benchmark: p95={p95*1000:.1f}ms; 30 sequential search/filter requests.')
        self.assertLess(p95,1.0)
