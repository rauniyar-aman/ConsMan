"""Validate an encrypted PostgreSQL + private-file backup in an isolated database."""
import argparse,hashlib,io,json,os,secrets,subprocess,sys,tarfile,tempfile,time
from pathlib import Path
from urllib.parse import quote
from cryptography.fernet import Fernet
import psycopg
from psycopg import sql
os.environ['PYTHONUTF8']='1'
BASE=Path(__file__).resolve().parents[1]
c=json.loads((BASE/'private_config/postgres.json').read_text())
BIN=Path(os.getenv('POSTGRES_BIN','C:/Program Files/PostgreSQL/18/bin'))
parser=argparse.ArgumentParser();parser.add_argument('--database',choices=['consman','consman_validation'],default='consman_validation');args=parser.parse_args();source_db=args.database;source_role='consman_migrator' if source_db=='consman' else 'consman_validator'
started=time.perf_counter()
def url(role,db):return 'postgresql://'+role+':'+quote(c[role],safe='')+'@127.0.0.1:5432/'+db
# Import fictional local data only into the isolated validation database.
if source_db=='consman_validation':
    fixture=BASE.parent/'tmp/postgres-acceptance.json'
    subprocess.run([sys.executable,'manage.py','dumpdata','auth.user','crm','qr','intake','data_import','--output',str(fixture)],cwd=BASE,check=True)
    env=dict(os.environ,DATABASE_URL=url(source_role,source_db),DJANGO_DEBUG='true',DJANGO_SECRET_KEY=c['django_secret'])
    subprocess.run([sys.executable,'manage.py','loaddata',str(fixture)],cwd=BASE,env=env,check=True)
files={str(p.relative_to(BASE/'private_media')).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in (BASE/'private_media').rglob('*') if p.is_file()}
with tempfile.TemporaryDirectory(prefix='consman-backup-') as temp:
    work=Path(temp).resolve();dump=work/'database.dump'
    backupenv=dict(os.environ,PGHOST='127.0.0.1',PGPORT='5432',PGUSER=source_role,PGPASSWORD=c[source_role])
    subprocess.run([str(BIN/'pg_dump.exe'),'-Fc','--file',str(dump),source_db],env=backupenv,check=True)
    manifest={'database_sha256':hashlib.sha256(dump.read_bytes()).hexdigest(),'private_files':files,'source_database':source_db}
    archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w') as tar:
        tar.add(dump,arcname='database.dump')
        data=json.dumps(manifest).encode();info=tarfile.TarInfo('manifest.json');info.size=len(data);tar.addfile(info,io.BytesIO(data))
        for name in files:tar.add(BASE/'private_media'/name,arcname='private_media/'+name)
    destination=BASE/'private_backups';destination.mkdir(exist_ok=True)
    backup=destination/('postgres-'+source_db+'-validated.enc');cipher=Fernet(c['backup_key'].encode());backup.write_bytes(cipher.encrypt(archive.getvalue()))
    restored=work/'restored';restored.mkdir()
    with tarfile.open(fileobj=io.BytesIO(cipher.decrypt(backup.read_bytes()))) as tar:
        for member in tar.getmembers():
            target=(restored/member.name).resolve()
            if not target.is_relative_to(restored) or not member.isfile():raise RuntimeError('Unsafe backup member')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(tar.extractfile(member).read())
    assert hashlib.sha256((restored/'database.dump').read_bytes()).hexdigest()==manifest['database_sha256']
    for name,digest in files.items():assert hashlib.sha256((restored/'private_media'/name).read_bytes()).hexdigest()==digest
    restoredb='consman_restore_'+secrets.token_hex(4)
    with psycopg.connect(host='127.0.0.1',dbname='postgres',user='postgres',autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(restoredb),sql.Identifier(source_role)))
        admin.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(sql.Identifier(restoredb)))
        admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {},consman_app').format(sql.Identifier(restoredb),sql.Identifier(source_role)))
    subprocess.run([str(BIN/'pg_restore.exe'),'--exit-on-error','--no-owner','--dbname',restoredb,str(restored/'database.dump')],env=backupenv,check=True)
    with psycopg.connect(url(source_role,source_db)) as source,psycopg.connect(url('consman_app',restoredb)) as target:
        counts={}
        tables=[r[0] for r in source.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
        for table in tables:
            q=sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table));a=source.execute(q).fetchone()[0];b=target.execute(q).fetchone()[0];assert a==b,table;counts[table]=b
        for verb in ['UPDATE crm_auditevent SET action=action','DELETE FROM crm_auditevent','TRUNCATE crm_auditevent']:
            try:
                with target.transaction():target.execute(verb)
            except psycopg.errors.InsufficientPrivilege:pass
            else:raise RuntimeError('Restored audit privileges are unsafe')
        # Validate foreign keys and the immutable trigger in the restored schema.
        assert target.execute("SELECT COUNT(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()[0]==0
        assert target.execute("SELECT COUNT(*) FROM pg_trigger WHERE tgname='audit_immutable'").fetchone()[0]==1
        assets=target.execute('SELECT png,payload FROM qr_qrasset WHERE decode_passed').fetchall()
        import zxingcpp
        from PIL import Image
        for filename,payload in assets:
            results=zxingcpp.read_barcodes(Image.open(restored/'private_media'/filename));assert any(r.text==payload for r in results)
    evidence={'restore_database':restoredb,'tables_checked':len(counts),'people':counts.get('crm_person',0),'audit_events':counts.get('crm_auditevent',0),'files_checked':len(files),'qr_assets_decoded':len(assets),'duration_seconds':round(time.perf_counter()-started,2),'backup':str(backup.relative_to(BASE.parent)).replace('\\','/'),'source_database':source_db,'encrypted':True,'runtime_audit_guards':'PASS','foreign_keys':'PASS'}
    (BASE.parent/('tmp/'+source_db+'-backup-results.json')).write_text(json.dumps(evidence,indent=2));print(json.dumps(evidence))
