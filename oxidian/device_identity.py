"""Identidad privada y persistente del navegador, separada de la sesión de acceso."""
import hashlib
import secrets
from flask import current_app, g, has_request_context, request, session
from itsdangerous import BadSignature, URLSafeTimedSerializer

DEVICE_COOKIE_MAX_AGE = 365 * 86400


def _device_cookie_name():
    return '__Host-oxidian_device' if current_app.config.get('SESSION_COOKIE_SECURE') else 'oxidian_device'


def _device_signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='oxidian-device-v1')


def browser_device_hash(create=False):
    if not has_request_context():
        return None
    key = None
    token = request.cookies.get(_device_cookie_name())
    if token:
        try:
            key = _device_signer().loads(token, max_age=DEVICE_COOKIE_MAX_AGE)
        except BadSignature:
            pass
    cookie_valid = isinstance(key, str) and len(key) >= 32
    if not cookie_valid:
        key = session.get('_push_device_key')
    if not isinstance(key, str) or len(key) < 32:
        if not create:
            return None
        key = secrets.token_urlsafe(32)
        session['_push_device_key'] = key
    if create and not cookie_valid:
        # Migra la identidad de sesiones existentes sin cambiar la vinculación.
        g.device_cookie_pending = key
    return hashlib.sha256(key.encode()).hexdigest()


def persist_browser_device(response):
    key = getattr(g, 'device_cookie_pending', None)
    if key:
        response.set_cookie(
            _device_cookie_name(), _device_signer().dumps(key),
            max_age=DEVICE_COOKIE_MAX_AGE, httponly=True,
            secure=bool(current_app.config.get('SESSION_COOKIE_SECURE')),
            samesite='Lax', path='/',
        )
    return response


def initialise_browser_session():
    if request.method == 'GET' and (request.path in {'/','/carrito','/checkout','/ayuda','/club','/auth/login','/acceso'} or request.path.startswith('/pedido/')):
        browser_device_hash(create=True)
        session.permanent = True
