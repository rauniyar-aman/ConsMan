from django.db import migrations


def create(apps, schema_editor):
    table = 'insights_assistancereview'
    if schema_editor.connection.vendor == 'sqlite':
        for verb in ['UPDATE', 'DELETE']:
            schema_editor.execute(f"CREATE TRIGGER {table}_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'Assistance reviews are immutable'); END")
    elif schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(f'CREATE TRIGGER {table}_guard BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION consman_audit_guard()')


def remove(apps, schema_editor):
    table = 'insights_assistancereview'
    if schema_editor.connection.vendor == 'sqlite':
        for verb in ['update', 'delete']: schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_{verb}')
    elif schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_guard ON {table}')


class Migration(migrations.Migration):
    dependencies = [('insights', '0001_initial')]
    operations = [migrations.RunPython(create, remove)]
