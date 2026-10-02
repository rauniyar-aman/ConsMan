from django.db import migrations


TABLES=['admissions_applicationevent','admissions_documentversion']
FIELDS=['destination','version','milestones','required_documents','offer_conditions','enrollment_requirements','visa_requirements','task_templates','created_by_id','created_at']


def guards(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    for table in TABLES:
        if vendor=='sqlite':
            for action in ['UPDATE','DELETE']:
                schema_editor.execute(f"CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Admissions history is append-only'); END")
        elif vendor=='postgresql':
            schema_editor.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION consman_audit_guard()')
    if vendor=='sqlite':
        changed=' OR '.join(f'OLD.{field} IS NOT NEW.{field}' for field in FIELDS)
        schema_editor.execute(f"CREATE TRIGGER admissions_workflow_immutable BEFORE UPDATE ON admissions_workflowtemplate WHEN {changed} BEGIN SELECT RAISE(ABORT,'Create a new workflow version'); END")
        schema_editor.execute("CREATE TRIGGER admissions_workflow_no_delete BEFORE DELETE ON admissions_workflowtemplate BEGIN SELECT RAISE(ABORT,'Deactivate workflow versions instead'); END")
    elif vendor=='postgresql':
        changed=' OR '.join(f'OLD.{field} IS DISTINCT FROM NEW.{field}' for field in FIELDS)
        schema_editor.execute(f"CREATE FUNCTION consman_workflow_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Deactivate workflow versions instead'; END IF; IF {changed} THEN RAISE EXCEPTION 'Create a new workflow version'; END IF; RETURN NEW; END; $$")
        schema_editor.execute('CREATE TRIGGER admissions_workflow_immutable BEFORE UPDATE OR DELETE ON admissions_workflowtemplate FOR EACH ROW EXECUTE FUNCTION consman_workflow_guard()')


def remove(apps,schema_editor):
    for table in TABLES:
        if schema_editor.connection.vendor=='sqlite':
            for action in ['update','delete']:schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_{action}')
        elif schema_editor.connection.vendor=='postgresql':schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}')
    if schema_editor.connection.vendor=='sqlite':
        for name in ['admissions_workflow_immutable','admissions_workflow_no_delete']:schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}')
    elif schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER IF EXISTS admissions_workflow_immutable ON admissions_workflowtemplate')
        schema_editor.execute('DROP FUNCTION IF EXISTS consman_workflow_guard()')


class Migration(migrations.Migration):
    dependencies=[('admissions','0001_initial'),('crm','0003_audit_immutability')]
    operations=[migrations.RunPython(guards,remove)]
