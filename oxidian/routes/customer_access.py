"""Entrada por teléfono registrado; nunca crea clientes desde el formulario."""
import time
from flask import Blueprint, flash, redirect, render_template, request, session, url_for, jsonify, current_app
from extensions import db, limiter
from customer_access import private_store_enabled, verified_customer, customer_grant, private_pwa_required
from device_identity import browser_device_hash
from loyalty_service import bloquear_cliente_puntos, solicitar_codigo
from models import User
from phone_utils import normalizar_telefono_cliente
from services import buscar_cliente_por_telefono

customer_access_bp = Blueprint('customer_access', __name__)


@customer_access_bp.route('/acceso', methods=['GET', 'POST'])
@limiter.limit('10 per minute') if limiter else (lambda f: f)
def enter():
    app_required = private_pwa_required()
    app_ready = bool(session.get('customer_pwa_ready'))
    if not private_store_enabled() or (verified_customer() and
            (not app_required or (app_ready and request.args.get('instalar') != '1'))):
        return redirect(url_for('public.index'))
    if app_required and request.method == 'POST' and not app_ready:
        flash('Instala y abre la aplicación para verificar tu teléfono.', 'info')
        return redirect(url_for('customer_access.enter'), code=303)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'request':
            previous = session.pop('customer_access_pending', None)
            phone = request.form.get('telefono', '').strip()[:40]
            customer, _ = buscar_cliente_por_telefono(phone)
            grant = customer_grant(customer)
            device_hash = browser_device_hash(create=True)
            if grant and (not grant.device_hash or grant.device_hash == device_hash):
                result = solicitar_codigo(customer, permitir_sin_puntos=True, identidad=True)
                if result.get('ok'):
                    session['customer_access_pending'] = {
                        'id': customer.id, 'phone': normalizar_telefono_cliente(customer.telefono),
                        'at': time.time(),
                    }
                elif (isinstance(previous, dict)
                      and previous.get('id') == customer.id
                      and previous.get('phone') == normalizar_telefono_cliente(customer.telefono)):
                    session['customer_access_pending'] = previous
            # Mismo paso y mensaje aunque el número no exista o esté inactivo.
            session['customer_access_code_step'] = True
            flash('Si tu teléfono tiene acceso, recibirás un código por WhatsApp. Revisa tus mensajes.', 'info')
        elif action == 'verify':
            pending = session.get('customer_access_pending') or {}
            customer = db.session.get(User, pending.get('id')) if pending.get('id') else None
            # El rol no filtra: cualquier usuario con grant activo puede entrar
            # como comprador. El rol se conserva; sólo se abre sesión de tienda.
            if customer and customer.activo:
                customer = bloquear_cliente_puntos(customer)
                grant = customer_grant(customer, lock=True)
                device_hash = browser_device_hash(create=True)
                if (grant and (not grant.device_hash or grant.device_hash == device_hash)
                        and normalizar_telefono_cliente(customer.telefono) == pending.get('phone')
                        and 0 <= time.time() - pending.get('at', 0) < 600
                        and customer.verificar_cod_puntos(request.form.get('codigo', '').strip(), consumir=True)):
                    grant.device_hash = device_hash
                    db.session.commit()
                    session['customer_access'] = {
                        'id': customer.id, 'phone': pending['phone'], 'at': time.time(),
                        'version': customer.mfa_session_version or 0,
                    }
                    session.permanent = True
                    session.pop('customer_access_pending', None)
                    session.pop('customer_access_code_step', None)
                    return redirect(url_for('public.index'), code=303)
                db.session.commit()  # conservar límite de intentos del OTP
            flash('No se pudo verificar el código. Revisa el código o solicita uno nuevo.', 'danger')
        elif action == 'restart':
            session.pop('customer_access_code_step', None)
        return redirect(url_for('customer_access.enter'), code=303)
    return render_template('public/customer_access.html', code_step=session.get('customer_access_code_step', False), app_required=app_required, app_ready=app_ready)


@customer_access_bp.post('/acceso/salir')
def leave():
    for key in ('customer_access', 'customer_access_pending', 'customer_access_code_step', 'cart_puntos'):
        session.pop(key, None)
    return redirect(url_for('customer_access.enter'), code=303)


@customer_access_bp.post('/acceso/app')
def open_app():
    # Es una señal de interfaz, no una credencial: la autorización y el OTP
    # siguen siendo obligatorios, aunque un cliente manipule esta señal.
    if (request.get_json(silent=True) or {}).get('standalone') is not True:
        return jsonify(ok=False), 400
    session['customer_pwa_ready'] = True
    return jsonify(ok=True, next=url_for('customer_access.enter'))


@customer_access_bp.get('/acceso/manifest.webmanifest')
def install_manifest():
    """Instalador sin catálogo, screenshots del menú ni enlaces internos."""
    from store_config import get_store_profile
    profile = get_store_profile()
    version = current_app.config['ASSET_VERSION']
    icon = profile.get('app_icon_url')
    icons = ([{'src':icon, 'sizes':'any', 'purpose':'any maskable'}] if icon else [
        {'src':f'/pwa-assets/{version}/pwa-icon-{size}.png', 'sizes':f'{size}x{size}', 'type':'image/png'}
        for size in (192,512)
    ])
    response = jsonify(name=profile['nombre'], short_name=profile['nombre'][:12],
        id='/', start_url='/acceso?source=pwa', scope='/', display='standalone',
        background_color='#fffaf0', theme_color='#172b3a', lang='es', icons=icons,
        description='Instala la aplicación y verifica tu teléfono para acceder.',
        prefer_related_applications=False)
    response.mimetype = 'application/manifest+json'
    return response
