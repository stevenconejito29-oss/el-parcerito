"""Identidad privada del navegador, independiente del teléfono del cliente."""
import hashlib
import secrets
from flask import has_request_context, session


def browser_device_hash(create=False):
    if not has_request_context():
        return None
    key = session.get('_push_device_key')
    if not isinstance(key,str) or len(key)<32:
        if not create:
            return None
        key=secrets.token_urlsafe(32)
        session['_push_device_key']=key
    return hashlib.sha256(key.encode()).hexdigest()


def initialise_browser_session():
    from flask import request
    if request.method == 'GET' and (request.path in {'/','/carrito','/checkout','/ayuda','/club','/auth/login'} or request.path.startswith('/pedido/')):
        browser_device_hash(create=True)
        # La app conserva la sesión al cerrar el navegador; los pedidos siguen
        # sujetos a su autorización y caducidad individual.
        session.permanent=True
