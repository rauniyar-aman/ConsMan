-- Run as the migration/table-owning role after migrations.
-- Create consman_app separately using a securely supplied password.
GRANT CONNECT ON DATABASE consman TO consman_app;
GRANT USAGE ON SCHEMA public TO consman_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO consman_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO consman_app;
REVOKE UPDATE, DELETE, TRUNCATE ON crm_auditevent FROM consman_app;
REVOKE CREATE ON SCHEMA public FROM consman_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
-- consman_app must not own tables, inherit consman_migrator, or be SUPERUSER.
-- Reapply this script after schema migrations; the trigger additionally rejects
-- UPDATE/DELETE even if a permission is accidentally granted later.

REVOKE UPDATE, DELETE, TRUNCATE ON admissions_applicationevent, admissions_documentversion FROM consman_app;
REVOKE DELETE, TRUNCATE ON admissions_workflowtemplate FROM consman_app;

REVOKE UPDATE, DELETE, TRUNCATE ON progression_visaevent, progression_visaworkflow FROM consman_app;

REVOKE UPDATE,DELETE,TRUNCATE ON finance_financeevent,finance_financefile,finance_receipt,finance_commissionreceipt,finance_commissionrule,finance_paymentallocation FROM consman_app;
