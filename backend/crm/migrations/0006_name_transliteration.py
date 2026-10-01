import re
from unidecode import unidecode
from django.db import migrations


def backfill(apps,schema_editor):
    Person=apps.get_model('crm','Person')
    for person in Person.objects.all().iterator():
        value=re.sub(r'^(mr|mrs|ms|miss|dr)\.?\s+','', ' '.join(person.full_name.casefold().split()))
        name=' '.join(sorted(re.findall(r'\w+',unidecode(value).casefold())))[:160]
        Person.objects.filter(pk=person.pk).update(normalized_name=name)


class Migration(migrations.Migration):
    dependencies=[('crm','0005_postgres_search_indexes')]
    operations=[migrations.RunPython(backfill,migrations.RunPython.noop)]
