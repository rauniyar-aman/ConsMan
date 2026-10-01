"""Provision isolated ConsMan databases on the existing local PostgreSQL server.
Passwords are kept in an ACL-restricted ignored file and are never printed.
Does not modify Gateway databases, accounts or environment files.
"""
import getpass,json,os,secrets,subprocess
from pathlib import Path
import psycopg
from psycopg import sql
from cryptography.fernet import Fernet

BASE=Path(__file__).resolve().parents[1]
private=BASE/'private_config'
private.mkdir(exist_ok=True)
account=os.environ.get('USERDOMAIN','')+'\\'+getpass.getuser()
if os.name=='nt':
    subprocess.run(['icacls',str(private),'/inheritance:r','/grant:r',account+':(OI)(CI)F','SYSTEM:(OI)(CI)F'],check=True,capture_output=True)
path=private/'postgres.json'
if path.exists():config=json.loads(path.read_text())
else:
    config={name:secrets.token_urlsafe(36) for name in ['consman_migrator','consman_app','consman_validator','django_secret','admin_password']}
    config['backup_key']=Fernet.generate_key().decode()
    config.update(host='127.0.0.1',port=5432,production_database='consman',validation_database='consman_validation')
    path.write_text(json.dumps(config,indent=2))
admin=dict(host=config['host'],port=config['port'],dbname='postgres',user='postgres',connect_timeout=5)
if os.getenv('PGADMIN_PASSWORD'):admin['password']=os.environ['PGADMIN_PASSWORD']
with psycopg.connect(**admin,autocommit=True) as connection:
    for role in ['consman_migrator','consman_app','consman_validator']:
        exists=connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
        if not exists:connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEROLE NOINHERIT NOBYPASSRLS {}').format(sql.Identifier(role),sql.Literal(config[role]),sql.SQL('CREATEDB' if role=='consman_validator' else 'NOCREATEDB')))
    for name,owner in [('consman','consman_migrator'),('consman_validation','consman_validator')]:
        if not connection.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone():
            connection.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name),sql.Identifier(owner)))
        connection.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
        connection.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}, consman_app').format(sql.Identifier(name),sql.Identifier(owner)))
    hba=Path(connection.execute('SHOW hba_file').fetchone()[0])
    original=hba.read_text()
    marker='# ConsMan SCRAM authentication (other applications unchanged)'
    if marker not in original:
        (private/'pg_hba.before-consman.conf').write_text(original)
        rules=marker+'\n'+''.join('host all consman_migrator,consman_app,consman_validator '+address+' scram-sha-256\n' for address in ['127.0.0.1/32','::1/128'])
        hba.write_text(rules+original)
        errors=connection.execute('SELECT error FROM pg_hba_file_rules WHERE error IS NOT NULL').fetchall()
        if errors:
            hba.write_text(original);raise RuntimeError('PostgreSQL rejected HBA rules; original restored')
        assert connection.execute('SELECT pg_reload_conf()').fetchone()[0]
try:
    psycopg.connect(host=config['host'],port=config['port'],dbname='consman',user='consman_app',password='wrong-password',connect_timeout=3)
except psycopg.OperationalError:print('SCRAM rejects incorrect ConsMan credentials: PASS')
else:raise RuntimeError('ConsMan authentication did not reject incorrect credentials')
print('ConsMan databases and restricted roles provisioned on local PostgreSQL. Private configuration: backend/private_config/postgres.json')
