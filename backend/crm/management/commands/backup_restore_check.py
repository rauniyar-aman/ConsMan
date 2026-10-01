import base64
import hashlib
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.db import connection

class Command(BaseCommand):
    help='Create an encrypted local SQLite backup and verify restoration in an isolated database.'
    def add_arguments(self,parser):
        parser.add_argument('--output',default=str(settings.BASE_DIR/'private_backups'/'consman.sqlite3.enc'))
    def handle(self,*args,**options):
        if connection.vendor!='sqlite':raise CommandError('Use the PostgreSQL backup/restore runbook for PostgreSQL; this command verifies local SQLite only.')
        output=Path(options['output']).resolve();output.parent.mkdir(parents=True,exist_ok=True)
        secret=__import__('os').environ.get('BACKUP_ENCRYPTION_KEY')
        if not secret:raise CommandError('Set BACKUP_ENCRYPTION_KEY to a Fernet key before creating a backup.')
        try:cipher=Fernet(secret.encode())
        except ValueError:raise CommandError('BACKUP_ENCRYPTION_KEY must be a valid Fernet key.')
        with tempfile.TemporaryDirectory(prefix='consman-restore-') as temp:
            snapshot=Path(temp)/'snapshot.sqlite3';restored=Path(temp)/'restored.sqlite3'
            connection.ensure_connection()
            with closing(sqlite3.connect(snapshot)) as destination:connection.connection.backup(destination)
            plaintext=snapshot.read_bytes();output.write_bytes(cipher.encrypt(plaintext))
            restored.write_bytes(cipher.decrypt(output.read_bytes()))
            if hashlib.sha256(plaintext).digest()!=hashlib.sha256(restored.read_bytes()).digest():raise CommandError('Restored checksum differs.')
            with closing(sqlite3.connect(restored)) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise CommandError('Restored database integrity check failed.')
                people=db.execute('SELECT COUNT(*) FROM crm_person').fetchone()[0]
                audit=db.execute('SELECT COUNT(*) FROM crm_auditevent').fetchone()[0]
                triggers=db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='crm_auditevent'").fetchone()[0]
                if triggers<2:raise CommandError('Audit immutability guards are missing in the restored database.')
                if audit:
                    try:db.execute('DELETE FROM crm_auditevent');raise CommandError('Audit immutability guard is missing in the restored database.')
                    except sqlite3.IntegrityError:pass
            self.stdout.write(self.style.SUCCESS(f'Encrypted backup and isolated restore verified: {people} people, {audit} audit events; checksum and integrity passed. Output: {output}'))
