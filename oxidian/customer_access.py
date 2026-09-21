"""Acceso privado del cliente: identidad verificada y revocable por perfil."""
import time
from flask import g, jsonify, redirect, request, session, url_for
from extensions import db
from models import SiteConfig, User, CustomerAccessGrant
from device_identity import browser_device_hash
from phone_utils import normalizar_telefono_cliente


def private_store_enabled():
    return SiteConfig.get('ACCESO_CLIENTES_REGISTRADOS', '0') == '1'


def customer_grant(customer, lock=False):
    if not customer or customer.rol != 'cliente' or not customer.activo:
        return None
    query = CustomerAccessGrant.query.filter_by(user_id=customer.id, activo=True)
    if lock:
        query = query.populate_existing().with_for_update()
    return query.first()


def grant_matches_device(grant):
    return bool(grant and grant.device_hash and grant.device_hash == browser_device_hash())


def verified_customer():
    identity = session.get('customer_access')
    if not isinstance(identity, dict):
        return None
    try:
        valid = 0 <= time.time() - float(identity['at']) < 30 * 86400
        customer = db.session.get(User, int(identity['id'])) if valid else None
    except (KeyError, ValueError, TypeError, OverflowError):
        customer = None
    if (customer and customer.rol == 'cliente' and customer.activo
            and normalizar_telefono_cliente(customer.telefono) == identity.get('phone')
            and (customer.mfa_session_version or 0) == identity.get('version', 0)
            and grant_matches_device(customer_grant(customer))):
        return customer
    session.pop('customer_access', None)
    return None


def enforce_customer_access():
    # Los paneles conservan sus permisos propios. Las APIs públicas se incluyen
    # por blueprint, sin una excepción genérica que permita saltarse el bloqueo.
    protected = request.blueprint in {'public', 'web_chat'} or request.endpoint == 'web_manifest'
    exempt = {'public.informacion_legal', 'public.pedido_confirmado',
              'public.estado_pedido_web', 'public.cancelar_pedido_web'}
    if not protected or request.endpoint in exempt or not private_store_enabled():
        return None
    g.private_customer_access = True
    if verified_customer():
        return None
    message = 'Verifica tu teléfono para acceder a la tienda.'
    if request.is_json or request.path.startswith('/api/') or request.endpoint == 'web_manifest':
        return jsonify(ok=False, msg=message, error=message,
                       access_url=url_for('customer_access.enter')), 403
    return redirect(url_for('customer_access.enter'), code=303)


def private_access_headers(response):
    if getattr(g, 'private_customer_access', False) or request.blueprint == 'customer_access':
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.vary.add('Cookie')
    return response
