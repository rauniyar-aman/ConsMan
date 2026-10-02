from django.db import migrations


def create(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    for table in ['communications_messagetemplate','communications_channelconsent','communications_messageevent']:
        if vendor=='sqlite':
            for verb in ['UPDATE','DELETE']:schema_editor.execute(f"CREATE TRIGGER {table}_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'Communication history is immutable'); END")
        elif vendor=='postgresql':schema_editor.execute(f'CREATE TRIGGER {table}_guard BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION consman_audit_guard()')
    guards={'communications_outboundmessage':['person_id','contact_id','template_id','consent_id','channel','recipient','subject','body'],'communications_inboundmessage':['channel','sender','body','provider_id']}
    for table,columns in guards.items():
        if vendor=='sqlite':
            condition=' OR '.join(f'OLD.{c} IS NOT NEW.{c}' for c in columns)
            schema_editor.execute(f"CREATE TRIGGER {table}_payload BEFORE UPDATE ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'Message content is immutable'); END")
        elif vendor=='postgresql':
            old=','.join('OLD.'+c for c in columns);new=','.join('NEW.'+c for c in columns)
            schema_editor.execute(f"CREATE FUNCTION {table}_payload_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF ({old}) IS DISTINCT FROM ({new}) THEN RAISE EXCEPTION 'Message content is immutable'; END IF; RETURN NEW; END $$")
            schema_editor.execute(f'CREATE TRIGGER {table}_payload BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {table}_payload_guard()')


def remove(apps,schema_editor):
    vendor=schema_editor.connection.vendor
    for table in ['communications_messagetemplate','communications_channelconsent','communications_messageevent']:
        if vendor=='sqlite':
            for verb in ['update','delete']:schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_{verb}')
        elif vendor=='postgresql':schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_guard ON {table}')
    for table in ['communications_outboundmessage','communications_inboundmessage']:
        if vendor=='sqlite':schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_payload')
        elif vendor=='postgresql':
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_payload ON {table}')
            schema_editor.execute(f'DROP FUNCTION IF EXISTS {table}_payload_guard()')


class Migration(migrations.Migration):
    dependencies=[('communications','0002_outboundmessage_attempts_outboundmessage_claimed_at_and_more')]
    operations=[migrations.RunPython(create,remove)]
