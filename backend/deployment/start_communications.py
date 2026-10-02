"""Start the separately hosted Redis-backed communication worker or scheduler."""
import argparse,os,sys
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

parser=argparse.ArgumentParser()
parser.add_argument('process',choices=['worker','beat'])
args=parser.parse_args()
if urlsplit(os.getenv('CELERY_BROKER_URL','')).scheme not in ['redis','rediss']:
    raise SystemExit('Set CELERY_BROKER_URL to a private Redis connection before starting communications.')
if not os.getenv('DATABASE_URL') or not os.getenv('DJANGO_SECRET_KEY'):
    raise SystemExit('Configure the same database and application secret as the web service.')
base=Path(__file__).resolve().parents[1]
command=[sys.executable,'-m','celery','-A','config',args.process,'--loglevel=WARNING']
if args.process=='worker':command+=['--pool=solo','--concurrency=1']
else:command+=['--schedule=/tmp/consman-celerybeat']
subprocess.run(command,cwd=base,check=True)
