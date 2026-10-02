from django.db import migrations


def create(apps,schema_editor):
    table='student_experience_studentmessage'
    if schema_editor.connection.vendor=='sqlite':
        for verb in ['UPDATE','DELETE']:schema_editor.execute(f"CREATE TRIGGER {table}_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'Student message history is immutable'); END")
    elif schema_editor.connection.vendor=='postgresql':schema_editor.execute(f'CREATE TRIGGER {table}_guard BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION consman_audit_guard()')


def remove(apps,schema_editor):
    table='student_experience_studentmessage'
    if schema_editor.connection.vendor=='sqlite':
        for verb in ['update','delete']:schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_{verb}')
    elif schema_editor.connection.vendor=='postgresql':schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_guard ON {table}')


class Migration(migrations.Migration):
    dependencies=[('student_experience','0001_initial'),('crm','0003_audit_immutability')]
    operations=[migrations.RunPython(create,remove)]
