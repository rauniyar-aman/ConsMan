"""Start ConsMan with private local PostgreSQL/Gateway configuration."""
import argparse,json,os,subprocess,sys
from pathlib import Path
from urllib.parse import quote
BASE=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--database',choices=['existing','acceptance','production'],default='existing');p.add_argument('--port',type=int,default=8000);p.add_argument('--development',action='store_true');args=p.parse_args()
env=dict(os.environ)
private=BASE/'private_config'
if args.database!='existing':
    c=json.loads((private/'postgres.json').read_text());db='consman_validation' if args.database=='acceptance' else 'consman'
    env['DATABASE_URL']='postgresql://consman_app:'+quote(c['consman_app'],safe='')+'@127.0.0.1:5432/'+db
    env['DJANGO_SECRET_KEY']=c['django_secret'];env['DJANGO_DEBUG']='true' if args.database=='acceptance' or args.development else 'false'
if (private/'gateway.json').exists():
    g=json.loads((private/'gateway.json').read_text());env.update(WHATSAPP_GATEWAY_URL=g['url'],WHATSAPP_GATEWAY_KEY=g['api_key'],WHATSAPP_GATEWAY_SESSION=g['session'],WHATSAPP_GATEWAY_WEBHOOK_SECRET=g['webhook_secret'])
subprocess.run([sys.executable,'manage.py','runserver','127.0.0.1:'+str(args.port),'--noreload'],cwd=BASE,env=env,check=True)
