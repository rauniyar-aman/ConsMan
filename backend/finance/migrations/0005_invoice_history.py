from django.db import migrations


def initialize(apps,schema_editor):
    Invoice=apps.get_model('finance','Invoice')
    for invoice in Invoice.objects.filter(status='ISSUED').select_related('person').iterator():
        invoice.issued_snapshot={'person_name':invoice.person.full_name,'person_reference':invoice.person.ref,'description':invoice.description,'amount':str(invoice.amount),'currency':invoice.currency,'due_on':str(invoice.due_on or ''),'issued_on':str(invoice.created_at.date())}
        invoice.save(update_fields=['issued_snapshot'])
    if schema_editor.connection.vendor=='sqlite':
        schema_editor.execute("CREATE TRIGGER finance_invoice_guard BEFORE UPDATE ON finance_invoice WHEN OLD.status<>'DRAFT' AND (NEW.status='DRAFT' OR OLD.amount<>NEW.amount OR OLD.currency<>NEW.currency OR OLD.description<>NEW.description OR OLD.due_on IS NOT NEW.due_on OR OLD.issued_snapshot<>NEW.issued_snapshot) BEGIN SELECT RAISE(ABORT,'Issued invoice financial details are immutable'); END")
    elif schema_editor.connection.vendor=='postgresql':
        schema_editor.execute("CREATE FUNCTION consman_invoice_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.status<>'DRAFT' AND (NEW.status='DRAFT' OR (OLD.amount,OLD.currency,OLD.description,OLD.due_on,OLD.issued_snapshot) IS DISTINCT FROM (NEW.amount,NEW.currency,NEW.description,NEW.due_on,NEW.issued_snapshot)) THEN RAISE EXCEPTION 'Issued invoice financial details are immutable'; END IF; RETURN NEW; END $$")
        schema_editor.execute('CREATE TRIGGER finance_invoice_guard BEFORE UPDATE ON finance_invoice FOR EACH ROW EXECUTE FUNCTION consman_invoice_guard()')


def remove(apps,schema_editor):
    if schema_editor.connection.vendor=='sqlite':schema_editor.execute('DROP TRIGGER IF EXISTS finance_invoice_guard')
    elif schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER IF EXISTS finance_invoice_guard ON finance_invoice')
        schema_editor.execute('DROP FUNCTION IF EXISTS consman_invoice_guard()')


class Migration(migrations.Migration):
    dependencies=[('finance','0004_invoice_issued_snapshot')]
    operations=[migrations.RunPython(initialize,remove)]
