import statistics,time,uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from django.contrib.auth.models import User
from django.db import connection,connections
from django.test import TransactionTestCase,tag
from rest_framework.test import APIClient
from .models import Branch,Source,StaffProfile,Person,ContactMethod

@tag('performance')
@skipUnless(connection.vendor=='postgresql','Concurrent acceptance requires PostgreSQL')
class ConcurrentReadAcceptance(TransactionTestCase):
    def test_thirty_concurrent_readers_at_twenty_thousand_records(self):
        branch=Branch.objects.create(name='Concurrent fixture',code='LOAD')
        source=Source.objects.create(name='Concurrent fixture')
        user=User.objects.create(username='concurrent-manager');StaffProfile.objects.create(user=user,role='MANAGER',branch=branch)
        people=[Person(id=uuid.uuid4(),ref=f'LOAD-{i:06d}',full_name=f'Concurrent Person {i:05d}',branch=branch,source=source,owner=user,preferred_country='Australia') for i in range(20000)]
        Person.objects.bulk_create(people,batch_size=1000)
        ContactMethod.objects.bulk_create([ContactMethod(person=p,type='PHONE',raw_value=f'+97798{i:08d}',normalized_value=f'+97798{i:08d}') for i,p in enumerate(people)],batch_size=1000)
        def reader(worker):
            client=APIClient();client.force_authenticate(user);samples=[]
            try:
                for i in range(5):
                    query=f'?search=Person+{worker*613:05d}' if i%2 else '?preferred_country=Australia&lead_status=NEW'
                    start=time.perf_counter();response=client.get('/api/v1/people/'+query);elapsed=time.perf_counter()-start
                    if response.status_code!=200:raise AssertionError('Concurrent read failed')
                    samples.append(elapsed)
                return samples
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=30) as pool:samples=[x for result in pool.map(reader,range(30)) for x in result]
        p95=statistics.quantiles(samples,n=20)[18]
        print(f'30 concurrent PostgreSQL readers, 20,000 records, 150 requests: p95={p95*1000:.1f}ms, errors=0')
        self.assertLess(p95,1.0,'Concurrent p95 exceeds the local acceptance target')
