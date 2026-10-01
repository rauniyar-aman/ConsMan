"""Run release preparation at startup on hosts without a pre-deploy phase."""
import os
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, str(BASE / 'deployment/release.py')], cwd=BASE, check=True)
    os.chdir(BASE)
    os.execvp('gunicorn', ['gunicorn', 'config.wsgi:application',
                         '--bind', '0.0.0.0:' + os.getenv('PORT', '10000'),
                         '--workers', '1', '--threads', '4', '--timeout', '60',
                         '--access-logfile', '-'])


if __name__ == '__main__':
    main()
