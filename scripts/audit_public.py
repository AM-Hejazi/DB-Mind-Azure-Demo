"""Audit the clean public file set without printing secret values.

Optional --private-env checks exact locally configured password/key values in
memory. It never writes those values or prints matching content.
"""
import argparse
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-env', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['git','ls-files','--cached','--others','--exclude-standard','-z'],
                            cwd=root,capture_output=True,check=True)
    files = sorted(set(filter(None,result.stdout.decode().split('\0'))))
    patterns = {'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'provider_key':r'\bsk-[A-Za-z0-9_-]{24,}\b',
        'github_token':r'\b(?:ghp_|github_pat_)[A-Za-z0-9_]{25,}\b',
        'azure_account_key':r'AccountKey=[A-Za-z0-9+/]{50,}={0,2}'}
    secrets = []
    if args.private_env:
        for line in args.private_env.read_text().splitlines():
            name,sep,value = line.partition('=')
            if sep and ('PASSWORD' in name or 'API_KEY' in name) and len(value)>=8:
                secrets.append(value.encode())
            if name=='APP_AUTH' and sep:
                secrets.extend(pair.split(':',1)[1].encode() for pair in value.split(',') if ':' in pair)
    findings = []
    forbidden_roots = ('data/database/','data/schemas/','data/embeddings/','Evaluation/','ev_logs/','var/','.azure/','.gradio/')
    for name in files:
        path = root/name
        if name.startswith(forbidden_roots) or (path.suffix=='.env') or name.startswith('.env.') and name!='.env.example':
            findings.append((name,'excluded asset'))
        raw = path.read_bytes()
        if any(secret in raw for secret in secrets):
            findings.append((name,'private configured credential'))
        try:
            content = raw.decode()
        except UnicodeError:
            continue
        for category,pattern in patterns.items():
            if re.search(pattern,content):findings.append((name,category))
    print('Audited files:',len(files))
    for name,category in findings:
        print('Finding:',name,'category:',category)
    print('Credential/forbidden-asset findings:',len(findings))
    raise SystemExit(bool(findings))


if __name__=='__main__':main()
