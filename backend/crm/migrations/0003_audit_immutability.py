from django.db import migrations

def guards(apps,schema_editor):
    db=schema_editor.connection
    if db.vendor=='sqlite':
        schema_editor.execute("CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON crm_auditevent BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END")
        schema_editor.execute("CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON crm_auditevent BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END")
    elif db.vendor=='postgresql':
        schema_editor.execute("CREATE OR REPLACE FUNCTION consman_audit_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Audit events are append-only'; END; $$")
        schema_editor.execute('CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON crm_auditevent FOR EACH ROW EXECUTE FUNCTION consman_audit_guard()')

def remove(apps,schema_editor):
    if schema_editor.connection.vendor=='sqlite':
        schema_editor.execute('DROP TRIGGER IF EXISTS audit_no_update');schema_editor.execute('DROP TRIGGER IF EXISTS audit_no_delete')
    elif schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER IF EXISTS audit_immutable ON crm_auditevent');schema_editor.execute('DROP FUNCTION IF EXISTS consman_audit_guard()')

class Migration(migrations.Migration):
    dependencies=[('crm','0002_lostreason_slarule_systempolicy_tag_activity_channel_and_more')]
    operations=[migrations.RunPython(guards,remove)]
