import hashlib,io,json,os,shutil,subprocess,tarfile,tempfile,uuid
from pathlib import Path
from urllib.parse import urlparse,unquote,parse_qs
from cryptography.fernet import Fernet
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand,CommandError
from django.utils import timezone

class Command(BaseCommand):
    help='Encrypt a PostgreSQL dump and private files; store an independently keyed backup.'
    def handle(self,*args,**options):
        url=os.getenv('BACKUP_DATABASE_URL');key=os.getenv('BACKUP_ENCRYPTION_KEY')
        if not url or not key:raise CommandError('BACKUP_DATABASE_URL and BACKUP_ENCRYPTION_KEY are required.')
        parsed=urlparse(url)
        if parsed.scheme not in ['postgres','postgresql']:raise CommandError('Backup requires PostgreSQL.')
        try:cipher=Fernet(key.encode())
        except ValueError:raise CommandError('Invalid backup encryption key.')
        executable=shutil.which('pg_dump')
        if not executable:raise CommandError('pg_dump must be installed on the backup service.')
        env=dict(os.environ,PGHOST=parsed.hostname,PGPORT=str(parsed.port or 5432),PGDATABASE=parsed.path.lstrip('/'),PGUSER=unquote(parsed.username or ''),PGPASSWORD=unquote(parsed.password or ''))
        query=parse_qs(parsed.query)
        if 'sslmode' in query:env['PGSSLMODE']=query['sslmode'][0]
        if 'channel_binding' in query:env['PGCHANNELBINDING']=query['channel_binding'][0]
        def filenames(prefix=''):
            dirs,files=default_storage.listdir(prefix)
            for name in files:yield (prefix+'/'+name).lstrip('/')
            for name in dirs:
                if prefix=='' and name=='encrypted-backups':continue
                yield from filenames((prefix+'/'+name).lstrip('/'))
        with tempfile.TemporaryDirectory(prefix='consman-backup-') as temporary:
            dump=Path(temporary)/'database.dump'
            result=subprocess.run([executable,'--format=custom','--file',str(dump)],env=env,capture_output=True)
            if result.returncode:raise CommandError('PostgreSQL dump failed; check the backup role, connection and client version.')
            manifest={'created_at':timezone.now().isoformat(),'database_sha256':hashlib.sha256(dump.read_bytes()).hexdigest(),'files':{}}
            archive=io.BytesIO()
            with tarfile.open(fileobj=archive,mode='w') as tar:
                tar.add(dump,arcname='database.dump')
                for name in filenames():
                    with default_storage.open(name,'rb') as source:data=source.read()
                    manifest['files'][name]=hashlib.sha256(data).hexdigest();item=tarfile.TarInfo('private_media/'+name);item.size=len(data);tar.addfile(item,io.BytesIO(data))
                data=json.dumps(manifest).encode();item=tarfile.TarInfo('manifest.json');item.size=len(data);tar.addfile(item,io.BytesIO(data))
            path=default_storage.save('encrypted-backups/'+timezone.now().strftime('%Y/%m/%d/')+str(uuid.uuid4())+'.enc',ContentFile(cipher.encrypt(archive.getvalue())))
            # Validate the uploaded encrypted bytes, independently of local temporary data.
            with default_storage.open(path,'rb') as stored:restored=cipher.decrypt(stored.read())
            if restored!=archive.getvalue():raise CommandError('Uploaded backup verification failed.')
        self.stdout.write(self.style.SUCCESS('Encrypted database/private-file backup stored and verified: '+path))
