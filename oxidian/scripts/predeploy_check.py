#!/usr/bin/env python3
"""Pre-deploy checks for production/Cosmos.

Valida secretos, HTTPS/cookies y los artefactos públicos mínimos de la PWA.
Usage:
  python3 scripts/predeploy_check.py
"""
import os
import sys
import ast
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
    warnings.append('Production PWA, push and HSTS require a final HTTPS public URL')

for artifact in (
    'static/sw.js',
    'static/pwa-icon-192.png',
    'static/pwa-icon-512.png',
    'static/pwa-icon-512-maskable.png',
    'static/pwa-screenshot-mobile.png',
    'static/pwa-screenshot-wide.png',
    'templates/base.html',
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

if os.environ.get('SIMULATE_EVO_SEND', '').strip() == '1':
    warnings.append('SIMULATE_EVO_SEND=1; WhatsApp sends are simulated, not real')

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
