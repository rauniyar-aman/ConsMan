from django.db import migrations
from django.utils import timezone

TABLES=['finance_financeevent','finance_financefile','finance_receipt','finance_commissionreceipt','finance_commissionrule','finance_paymentallocation']


def initialize(apps,schema_editor):
    Payment=apps.get_model('progression','Payment');Receipt=apps.get_model('finance','Receipt');Counter=apps.get_model('progression','JourneyCounter')
    counter,_=Counter.objects.get_or_create(kind='REC',year=timezone.localdate().year)
    for payment in Payment.objects.filter(status='VERIFIED').select_related('person__branch').iterator():
        counter.value+=1
        Receipt.objects.create(reference=f'REC-{counter.year}-{counter.value:06d}',payment=payment,created_by_id=payment.verified_by_id or payment.created_by_id,snapshot={'person_name':payment.person.full_name,'person_reference':payment.person.ref,'payment_reference':payment.ref,'amount':str(payment.amount),'currency':payment.currency,'paid_on':str(payment.paid_on),'method':payment.method,'transaction_reference':payment.reference,'purpose':payment.purpose,'branch_name':payment.person.branch.name})
    counter.save()
    for table in TABLES:
        if schema_editor.connection.vendor=='sqlite':
            for verb in ['UPDATE','DELETE']:schema_editor.execute(f"CREATE TRIGGER {table}_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'Financial history is immutable'); END")
        elif schema_editor.connection.vendor=='postgresql':schema_editor.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION consman_audit_guard()')


def remove(apps,schema_editor):
    for table in TABLES:
        if schema_editor.connection.vendor=='sqlite':
            for verb in ['update','delete']:schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_{verb}')
        elif schema_editor.connection.vendor=='postgresql':schema_editor.execute(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}')


class Migration(migrations.Migration):
    dependencies=[('finance','0002_commissionreceipt_financeevent_financefile_receipt_and_more')]
    operations=[migrations.RunPython(initialize,remove)]
