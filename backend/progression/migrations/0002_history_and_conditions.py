from django.db import migrations


def initialize(apps,schema_editor):
    Offer=apps.get_model('admissions','OfferReceipt');Condition=apps.get_model('progression','OfferCondition')
    for offer in Offer.objects.all().iterator():
        Condition.objects.bulk_create([Condition(offer=offer,title=title) for title in offer.conditions])
    if schema_editor.connection.vendor=='sqlite':
        for action in ['UPDATE','DELETE']:
            schema_editor.execute(f"CREATE TRIGGER visa_event_{action.lower()} BEFORE {action} ON progression_visaevent BEGIN SELECT RAISE(ABORT,'Visa history is append-only'); END")
        schema_editor.execute("CREATE TRIGGER visa_workflow_no_update BEFORE UPDATE ON progression_visaworkflow BEGIN SELECT RAISE(ABORT,'Create a new visa workflow version'); END")
        schema_editor.execute("CREATE TRIGGER visa_workflow_no_delete BEFORE DELETE ON progression_visaworkflow BEGIN SELECT RAISE(ABORT,'Visa workflow versions are permanent'); END")
    elif schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('CREATE TRIGGER visa_event_immutable BEFORE UPDATE OR DELETE ON progression_visaevent FOR EACH ROW EXECUTE FUNCTION consman_audit_guard()')
        schema_editor.execute('CREATE TRIGGER visa_workflow_immutable BEFORE UPDATE OR DELETE ON progression_visaworkflow FOR EACH ROW EXECUTE FUNCTION consman_audit_guard()')


def remove(apps,schema_editor):
    if schema_editor.connection.vendor=='sqlite':
        for name in ['visa_event_update','visa_event_delete','visa_workflow_no_update','visa_workflow_no_delete']:schema_editor.execute(f'DROP TRIGGER IF EXISTS {name}')
    elif schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER IF EXISTS visa_event_immutable ON progression_visaevent')
        schema_editor.execute('DROP TRIGGER IF EXISTS visa_workflow_immutable ON progression_visaworkflow')


class Migration(migrations.Migration):
    dependencies=[('progression','0001_initial')]
    operations=[migrations.RunPython(initialize,remove)]
