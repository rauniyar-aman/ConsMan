from django.db import migrations


def restore(apps,schema_editor):
    if schema_editor.connection.vendor=='sqlite':
        for verb in ['UPDATE','DELETE']:schema_editor.execute(f"CREATE TRIGGER IF NOT EXISTS admissions_documentversion_{verb.lower()} BEFORE {verb} ON admissions_documentversion BEGIN SELECT RAISE(ABORT,'Admissions history is append-only'); END")


class Migration(migrations.Migration):
    dependencies=[('admissions','0003_document_student_visible_and_more')]
    operations=[migrations.RunPython(restore,migrations.RunPython.noop)]
