"""Entrada por teléfono registrado; nunca crea clientes desde el formulario."""
import time
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from extensions import db, limiter
from customer_access import private_store_enabled, verified_customer, customer_grant
from device_identity import browser_device_hash
from loyalty_service import bloquear_cliente_puntos, solicitar_codigo
from models import User
from phone_utils import normalizar_telefono_cliente
from services import buscar_cliente_por_telefono

customer_access_bp = Blueprint('customer_access', __name__)


@customer_access_bp.route('/acceso', methods=['GET', 'POST'])
@limiter.limit('10 per minute') if limiter else (lambda f: f)
def enter():
    if not private_store_enabled() or verified_customer():
        return redirect(url_for('public.index'))
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
            if customer and customer.activo and customer.rol == 'cliente':
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
    return render_template('public/customer_access.html', code_step=session.get('customer_access_code_step', False))


@customer_access_bp.post('/acceso/salir')
def leave():
    for key in ('customer_access', 'customer_access_pending', 'customer_access_code_step', 'cart_puntos'):
        session.pop(key, None)
    return redirect(url_for('customer_access.enter'), code=303)
