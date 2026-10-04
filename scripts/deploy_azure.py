"""Deploy a reviewed digest to existing demo infrastructure; secrets stay in memory.

Network/write operation. Uses the owner's Azure CLI session and ARM securestring
parameters. Never creates/seeds SQL, changes its firewall, or grants SQL roles.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_VALUES = []


def safe_message(value):
    text = str(value)
    for secret in PRIVATE_VALUES:
        if secret:
            for variant in (secret, json.dumps(secret)[1:-1], urllib.parse.quote(secret, safe='')):
                text = text.replace(variant, '[REDACTED]')
    return text


def az(*args):
    result = subprocess.run(['az', *args, '--only-show-errors', '-o', 'json'],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError('Azure CLI operation failed: ' + ' '.join(args[:3]))
    return json.loads(result.stdout) if result.stdout.strip() else None


def private_environment(path):
    values = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            name, separator, value = line.partition('=')
            if not separator:
                raise ValueError('Private environment file must use Docker KEY=value syntax')
            values[name] = value
    return values


def request(url, token, body=None):
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method='PUT' if body is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # ARM error messages can echo inputs. Report only status and error code.
        try:
            detail = json.loads(error.read()).get('error', {})
            code = detail.get('code', 'unknown')
        except (ValueError, AttributeError):
            code = 'unknown'
        raise RuntimeError(f'ARM request failed: HTTP {error.code}, code {code}') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path.home()/'dbmind-azure.env')
    parser.add_argument('--resource-group', required=True)
    parser.add_argument('--registry', required=True)
    parser.add_argument('--environment', required=True)
    parser.add_argument('--identity', required=True)
    parser.add_argument('--app', default='dbmind-demo')
    parser.add_argument('--image', required=True, help='Registry/repository@sha256:digest')
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--mode', choices=['mock', 'live'], default='mock')
    parser.add_argument('--db-auth', choices=['sql_password', 'managed_identity'], default='sql_password')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9.-]+/[a-z0-9/_-]+@sha256:[0-9a-f]{64}', args.image):
        parser.error('An immutable registry image digest is required')
    if not re.fullmatch(r'[0-9a-f]{40}', args.source_commit):
        parser.error('A full source commit is required')
    env = private_environment(args.env_file)
    # Validate without opening SQL or constructing a provider client.
    from src.access import load_access_settings
    from src.settings import load_settings
    from src.llm_settings import load_llm_settings
    settings = load_settings(env)
    access = load_access_settings(env)
    users = tuple(pair for pair in access.users if pair[0] not in access.admins)
    load_llm_settings(env)
    if settings.profile not in {'azure_sql','azure_sql_custom'} or not users:
        raise ValueError('Azure SQL and private application authentication are required')
    if settings.profile == 'azure_sql_custom' and args.mode != 'live':
        raise ValueError('Custom databases require --mode live; fixture mock responses are disabled')
    if not env.get('DEEPSEEK_API_KEY'):
        raise ValueError('Private DeepSeek key is required; its value is never displayed')
    account = az('account', 'show')
    group = az('group', 'show', '-n', args.resource_group)
    registry = az('acr', 'show', '-g', args.resource_group, '-n', args.registry)
    environment = az('containerapp', 'env', 'show', '-g', args.resource_group, '-n', args.environment)
    identity = az('identity', 'show', '-g', args.resource_group, '-n', args.identity)
    if not args.image.startswith(registry['loginServer'] + '/'):
        raise ValueError('Image digest must belong to the selected registry')
    values = {'appName': args.app, 'location': environment['location'],
        'environmentId': environment['id'], 'identityId': identity['id'],
        'identityClientId': identity['clientId'], 'registryServer': registry['loginServer'],
        'image': args.image, 'dbServer': settings.server, 'dbName': settings.database,
        'dbProfile': settings.profile, 'dbSchemaScope': ','.join(settings.schemas),
        'dbTableScope': ','.join(settings.table_scope), 'dbSampleRows': settings.sample_rows,
        'dbSampleValueChars': settings.sample_value_chars, 'dbSampleTotalBytes': settings.sample_total_bytes,
        'dbAuth': args.db_auth, 'sqlUser': settings.username,
        'sqlPassword': settings.password if args.db_auth == 'sql_password' else '',
        'appAuth': ','.join(user+':'+password for user,password in users),
        'deepseekKey': env['DEEPSEEK_API_KEY'], 'llmMode': args.mode,
        'sourceCommit': args.source_commit,
        'appAdminUsername': env.get('APP_ADMIN_USERNAME',''),
        'appAdminPassword': env.get('APP_ADMIN_PASSWORD','')}
    token = az('account', 'get-access-token', '--resource', 'https://management.azure.com/')['accessToken']
    PRIVATE_VALUES.extend([values['appAdminPassword'], token, values['sqlPassword'], values['appAuth'],
                           values['deepseekKey'], *[password for _, password in users]])
    url = ('https://management.azure.com/subscriptions/' + account['id']
        + '/resourceGroups/' + group['name'] + '/providers/Microsoft.Resources/deployments/dbmind-demo-app'
        + '?api-version=2022-09-01')
    template = json.loads((ROOT/'infra/container-app.json').read_text())
    body = {'properties': {'mode': 'Incremental', 'template': template,
        'parameters': {key: {'value': value} for key,value in values.items()}}}
    request(url, token, body)
    deadline = time.monotonic()+1200
    while time.monotonic() < deadline:
        state = request(url, token)['properties']
        status = state['provisioningState']
        print('Deployment state:', status, flush=True)
        if status == 'Succeeded':
            print(json.dumps({'url': state['outputs']['url']['value'], 'image': args.image,
                              'source_commit': args.source_commit, 'mode': args.mode, 'db_auth': args.db_auth}, indent=2))
            return
        if status in {'Failed', 'Canceled'}:
            error = state.get('error', {})
            print('Deployment error code:', error.get('code', 'unknown'))
            raise RuntimeError('Deployment failed; inspect sanitized deployment operation diagnostics')
        time.sleep(30)
    raise RuntimeError('Deployment exceeded its bounded wait; inspect actual Azure state')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No traceback/local variables or arbitrary provider error text.
        print('Deployment stopped:', type(error).__name__)
        if isinstance(error, RuntimeError):
            print(str(error))  # RuntimeError messages above contain only safe codes/operations.
        raise SystemExit(1)
