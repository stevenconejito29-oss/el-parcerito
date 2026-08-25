#!/usr/bin/env python3
"""Pre-deploy checks for production/Cosmos.

Valida secretos, HTTPS/cookies y los artefactos públicos mínimos de la PWA.
Usage:
  python3 scripts/predeploy_check.py
"""
import os
import sys
import ast
import argparse
from pathlib import Path

root = Path(__file__).resolve().parents[1]
errors = []
warnings = []
PLACEHOLDER_MARKERS = (
    'CAMBIA_ESTO',
    'change-me',
    'local-dev',
    'dev-key',
    'insecure',
)


def load_env_file(path):
    """Carga KEY=VALUE sin ejecutar el archivo como código de shell."""
    env_path = Path(path).expanduser().resolve()
    if not env_path.is_file():
        raise FileNotFoundError(f'env file not found: {env_path}')
    for line_number, raw in enumerate(env_path.read_text(encoding='utf-8').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        if '=' not in line:
            raise ValueError(f'invalid env line {line_number}: expected KEY=VALUE')
        key, value = line.split('=', 1)
        key = key.strip()
        if not key or not key.replace('_', '').isalnum() or key[0].isdigit():
            raise ValueError(f'invalid env key at line {line_number}')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--env-file', help='Configuración real que se validará antes del deploy')
parser.add_argument('--deployment', choices=('cosmos',), help='Completa variables internas definidas por el stack')
args = parser.parse_args()
if args.env_file:
    try:
        load_env_file(args.env_file)
    except (OSError, ValueError) as exc:
        print(f'Cannot load environment: {exc}', file=sys.stderr)
        sys.exit(2)
if args.deployment == 'cosmos':
    # cosmos-compose.yml construye estas URLs dentro del contenedor; se
    # reproducen aquí para validar exactamente el contrato efectivo.
    db_user = os.environ.get('OXIDIAN_DB_USER', 'oxidian')
    db_password = os.environ.get('OXIDIAN_DB_PASSWORD', '')
    db_name = os.environ.get('OXIDIAN_DB_NAME', 'oxidian')
    if db_password:
        os.environ.setdefault(
            'DATABASE_URL',
            f'postgresql://{db_user}:{db_password}@oxidian-db:5432/{db_name}',
        )
    os.environ.setdefault('REDIS_URL', 'redis://oxidian-redis:6379/0')


def is_placeholder(value):
    low = (value or '').lower()
    return any(marker.lower() in low for marker in PLACEHOLDER_MARKERS)


def require_secret(name, min_len=16):
    value = os.environ.get(name, '')
    if not value:
        errors.append(f'{name} not set (required in production)')
        return
    if len(value) < min_len:
        errors.append(f'{name} is too short (<{min_len} chars)')
    if is_placeholder(value):
        errors.append(f'{name} still looks like a placeholder')

def require_file(relative_path):
    path = root / relative_path
    if not path.is_file() or path.stat().st_size == 0:
        errors.append(f'{relative_path} is missing or empty')


def check_duplicate_route_endpoints():
    """Detecta nombres de endpoint repetidos dentro del mismo blueprint."""
    for route_file in sorted((root / 'routes').glob('*.py')):
        try:
            source = route_file.read_text(encoding='utf-8')
            tree = ast.parse(source, filename=str(route_file))
        except (OSError, SyntaxError) as exc:
            errors.append(f'cannot inspect {route_file.relative_to(root)}: {exc}')
            continue

        endpoints = {}
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            has_route = any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == 'route'
                for decorator in node.decorator_list
            )
            if not has_route:
                continue
            previous = endpoints.get(node.name)
            if previous is not None:
                errors.append(
                    f'duplicate Flask endpoint {route_file.relative_to(root)}:'
                    f'{previous} and {node.lineno} ({node.name})'
                )
            else:
                endpoints[node.name] = node.lineno


def check_state_changes_are_not_get_routes():
    """Impide reintroducir mutaciones navegables sin CSRF por método GET."""
    action_segments = {
        'toggle', 'eliminar', 'borrar', 'cancelar', 'pagar', 'confirmar',
        'rechazar', 'devolver', 'reset', 'claim', 'release', 'close',
    }
    for route_file in sorted((root / 'routes').glob('*.py')):
        try:
            tree = ast.parse(route_file.read_text(encoding='utf-8'), filename=str(route_file))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in {'route', 'get'}
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                ):
                    continue
                route = str(decorator.args[0].value).rstrip('/')
                segment = route.rsplit('/', 1)[-1].lower()
                if segment not in action_segments:
                    continue
                methods = None
                for keyword in decorator.keywords:
                    if keyword.arg == 'methods':
                        try:
                            methods = {str(value).upper() for value in ast.literal_eval(keyword.value)}
                        except (TypeError, ValueError, SyntaxError):
                            methods = set()
                allows_get = decorator.func.attr == 'get' or methods is None or 'GET' in methods
                if allows_get:
                    errors.append(
                        f'state-changing route allows GET {route_file.relative_to(root)}:'
                        f'{node.lineno} ({route})'
                    )


# 1. SECRET_KEY
require_secret('SECRET_KEY', 32)

# 2. DATABASE_URL
db_url = os.environ.get('DATABASE_URL', '')
if not db_url:
    errors.append('DATABASE_URL not set')
else:
    if not db_url.startswith(('postgresql://', 'postgresql+psycopg://')):
        errors.append('DATABASE_URL must use PostgreSQL')

# 4. presence of helper script
if not (root / 'scripts' / 'set_bot_siteconfig.py').exists():
    warnings.append('oxidian/scripts/set_bot_siteconfig.py not found')

# 4b. Integration secrets. These must exist before exposing the single domain.
require_secret('BOT_API_KEY', 16)
require_secret('BOT_PANEL_KEY', 16)
require_secret('WEBHOOK_SECRET', 32)
require_secret('EVOLUTION_API_KEY', 16)
require_secret('OXIDIAN_DB_PASSWORD', 16)
require_secret('EVOLUTION_DB_PASSWORD', 16)

public_url = (os.environ.get('OXIDIAN_PUBLIC_URL') or '').strip()
cookie_secure = os.environ.get('SESSION_COOKIE_SECURE', '1').strip().lower() not in {
    '0', 'false', 'no', 'off',
}
if public_url.startswith('https://') and not cookie_secure:
    errors.append('SESSION_COOKIE_SECURE must be enabled for an HTTPS OXIDIAN_PUBLIC_URL')
elif public_url.startswith('http://') and cookie_secure:
    warnings.append('Secure cookies will not work over the configured HTTP public URL')
elif not public_url:
    errors.append('OXIDIAN_PUBLIC_URL not set')
elif not public_url.startswith(('http://', 'https://')):
    errors.append('OXIDIAN_PUBLIC_URL must be an absolute HTTP(S) URL')

if not public_url.startswith('https://'):
    errors.append('Production PWA, push and HSTS require a final HTTPS public URL')

redis_url = (os.environ.get('REDIS_URL') or '').strip()
if not redis_url.startswith(('redis://', 'rediss://')):
    errors.append('REDIS_URL must use Redis in production so rate limits work across workers')

for artifact in (
    'static/sw.js',
    'static/pwa-icon-192.png',
    'static/pwa-icon-512.png',
    'static/pwa-icon-512-maskable.png',
    'static/pwa-icon-monochrome.svg',
    'static/pwa-badge-96.png',
    'static/pwa-screenshot-mobile.png',
    'static/pwa-screenshot-wide.png',
    'templates/base.html',
    'static/js/pwa-manager.js',
):
    require_file(artifact)

sw_source = (root / 'static' / 'sw.js').read_text(encoding='utf-8')
for unsafe_entry in ('"/"', '"/manifest.webmanifest"'):
    precache = sw_source.split('const PRECACHE = [', 1)[-1].split('];', 1)[0]
    if unsafe_entry in precache:
        errors.append(f'service worker must not precache personalized resource {unsafe_entry}')
if 'event.request.mode === "navigate"' not in sw_source:
    errors.append('service worker must keep HTML navigations network-only')

check_duplicate_route_endpoints()
check_state_changes_are_not_get_routes()

if os.environ.get('SIMULATE_EVO_SEND', '').strip() == '1':
    errors.append('SIMULATE_EVO_SEND=1; WhatsApp sends are simulated, not real')

# Contratos críticos que no dependen de la base de datos.
ticket_source = (root / 'templates' / 'pos' / 'ticket.html').read_text(encoding='utf-8')
if '@page { size: 48mm 200mm' not in ticket_source or 'width: 48mm' not in ticket_source:
    errors.append('ticket template is not configured for the 48mm printable head of 58mm paper')
if 'pedido.puntos_usados /' in ticket_source:
    errors.append('ticket still converts loyalty points to money')

app_source = (root / 'app.py').read_text(encoding='utf-8')
if 'limiter.exempt(api_bot_bp)' in app_source:
    errors.append('bot API blueprint bypasses production rate limiting')

# Output summary
print('\nPre-deploy check summary:')
if errors:
    print('\nErrors:')
    for e in errors:
        print(' -', e)
else:
    print('\nNo blocking errors found.')

if warnings:
    print('\nWarnings:')
    for w in warnings:
        print(' -', w)

if not errors:
    print('\nReady to deploy to production (simulated).')
else:
    print('\nFix the errors above before deploying.')

sys.exit(1 if errors else 0)
