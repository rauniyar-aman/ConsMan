"""Migrate production/local acceptance databases and run the PostgreSQL suite."""
import json,os,subprocess,sys
from pathlib import Path
from urllib.parse import quote
import psycopg
BASE=Path(__file__).resolve().parents[1]
c=json.loads((BASE/'private_config/postgres.json').read_text())
def url(role,db):return 'postgresql://'+role+':'+quote(c[role],safe='')+'@127.0.0.1:5432/'+db
for role,db in [('consman_migrator','consman'),('consman_validator','consman_validation')]:
    env=dict(os.environ,DATABASE_URL=url(role,db),DJANGO_SECRET_KEY=c['django_secret'],DJANGO_DEBUG='false' if db=='consman' else 'true')
    subprocess.run([sys.executable,'manage.py','migrate','--noinput'],cwd=BASE,env=env,check=True)
    subprocess.run([sys.executable,'manage.py','seed_defaults'],cwd=BASE,env=env,check=True)
    with psycopg.connect(url(role,db),autocommit=True) as conn:
        script=(BASE/'deployment/app_role.sql').read_text().replace('CONNECT ON DATABASE consman TO','CONNECT ON DATABASE '+db+' TO')
        conn.execute(script)
        if db=='consman':
            conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC; GRANT USAGE ON SCHEMA public TO consman_app")
    with psycopg.connect(url('consman_app',db)) as conn:
        for verb in ['UPDATE crm_auditevent SET action=action','DELETE FROM crm_auditevent','TRUNCATE crm_auditevent','CREATE TABLE public.forbidden_test(id int)']:
            try:
                with conn.transaction():conn.execute(verb)
            except psycopg.errors.InsufficientPrivilege:pass
            else:raise AssertionError('Runtime privilege violation: '+verb)
        print(db+': app audit UPDATE/DELETE/TRUNCATE and schema CREATE denied: PASS')
env=dict(os.environ,DATABASE_URL=url('consman_validator','consman_validation'),DJANGO_SECRET_KEY=c['django_secret'],DJANGO_DEBUG='true')
subprocess.run([sys.executable,'manage.py','test','crm','intake','admissions','progression','--verbosity','1','--noinput'],cwd=BASE,env=env,check=True)
