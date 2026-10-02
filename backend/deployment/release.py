"""Run once per release with the migration credential, then apply runtime grants."""
import os,subprocess,sys
from pathlib import Path
import psycopg
from psycopg import sql
BASE=Path(__file__).resolve().parents[1]
url=os.environ.get('MIGRATION_DATABASE_URL')
if not url:raise RuntimeError('MIGRATION_DATABASE_URL is required for release migrations')
env=dict(os.environ,DATABASE_URL=url)
subprocess.run([sys.executable,'manage.py','migrate','--noinput'],cwd=BASE,env=env,check=True)
subprocess.run([sys.executable,'manage.py','seed_defaults'],cwd=BASE,env=env,check=True)
with psycopg.connect(url,autocommit=True) as conn:
 role=sql.Identifier(os.getenv('APP_DATABASE_ROLE','consman_app'))
 for statement in ['GRANT USAGE ON SCHEMA public TO {}','GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}','GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}','REVOKE UPDATE,DELETE,TRUNCATE ON crm_auditevent FROM {}','REVOKE UPDATE,DELETE,TRUNCATE ON admissions_applicationevent,admissions_documentversion FROM {}','REVOKE DELETE,TRUNCATE ON admissions_workflowtemplate FROM {}','REVOKE UPDATE,DELETE,TRUNCATE ON progression_visaevent,progression_visaworkflow FROM {}','REVOKE UPDATE,DELETE,TRUNCATE ON finance_financeevent,finance_financefile,finance_receipt,finance_commissionreceipt,finance_commissionrule,finance_paymentallocation FROM {}','REVOKE UPDATE,DELETE,TRUNCATE ON communications_messagetemplate,communications_channelconsent,communications_messageevent FROM {}','REVOKE DELETE,TRUNCATE ON communications_outboundmessage,communications_inboundmessage FROM {}','REVOKE CREATE ON SCHEMA public FROM {}']:
  conn.execute(sql.SQL(statement).format(role))
 conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
print('Migration and runtime-role grants completed.')
