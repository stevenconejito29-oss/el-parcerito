"""Servidor QA local con datos sintéticos, CSRF y CSP reales; jamás envía WhatsApp."""
import os
import re
import runpy
from pathlib import Path
from unittest.mock import patch

scope = runpy.run_path(str(Path(__file__).with_name('review_role_views.py')))
app, db, users = scope['app'], scope['db'], scope['users']
from models import User, SiteConfig, CustomerAccessGrant, ZonaEntrega, Stock, ProductExtraGroup, ProductExtraOption
with app.app_context():
    customer = db.session.get(User, users['cliente'])
    customer.telefono = customer.telefono_normalizado = '+34600000000'
    db.session.add(CustomerAccessGrant(user_id=customer.id, approved_by=users['super_admin'], activo=True))
    for key, value in {'ACCESO_CLIENTES_REGISTRADOS':'0', 'PREAPERTURA_ACTIVA':'0', 'HORARIO_MODO':'24h', 'WHATSAPP_COUNTRY_CODE':'34', 'delivery_franjas_activo':'0', 'delivery_inmediato_activo':'1'}.items():
        SiteConfig.set(key, value)
    SiteConfig.set('DIRECCION_NEGOCIO', 'Calle de prueba 12')
    db.session.add(ZonaEntrega(nombre='Zona QA', precio_envio=2, activo=True))
    db.session.add(Stock(producto_id=scope['product_id'], cantidad=100))
    extras = ProductExtraGroup(producto_id=scope['product_id'], nombre='Extras QA', tipo='extra', min_selecciones=0, max_selecciones=2)
    flavors = ProductExtraGroup(producto_id=scope['product_id'], nombre='Sabor QA', tipo='sabor', min_selecciones=1, max_selecciones=1)
    db.session.add_all([extras, flavors]); db.session.flush()
    db.session.add_all([
        ProductExtraOption(grupo_id=extras.id,nombre='Queso QA',precio=1.5,max_cantidad=2),
        ProductExtraOption(grupo_id=flavors.id,nombre='Mango QA',precio=0,max_cantidad=1),
    ])
    from models import Product, ComboItem, ComboGroup, DeliverySlot, SlotRepartidor, Order
    from business_time import business_today
    from datetime import time
    slot = DeliverySlot(fecha=business_today(),hora_inicio=time(0),hora_fin=time(23,59),capacidad_max=8,max_repartidores=1,activo=True)
    db.session.add(slot); db.session.flush()
    db.session.add(SlotRepartidor(slot_id=slot.id,repartidor_id=users['repartidor']))
    for order in Order.query.filter(Order.estado.in_(['armando','listo']),Order.tipo_entrega_cliente=='delivery').limit(2):
        order.slot_id = slot.id
    Path('/tmp/parcerito-flow-qa-slot').write_text(str(slot.id))
    from models import Product, ComboItem, ComboGroup
    drink = Product(nombre='Bebida combo QA', precio=2, activo=True)
    combo = Product(nombre='Combo completo QA', precio=12, activo=True, es_combo=True)
    db.session.add_all([drink,combo]); db.session.flush()
    db.session.add(Stock(producto_id=drink.id,cantidad=100))
    fixed = ComboGroup(combo_id=combo.id,nombre='Incluido',tipo='fijo')
    choice = ComboGroup(combo_id=combo.id,nombre='Bebidas',tipo='seleccion',min_selecciones=2,max_selecciones=2)
    db.session.add_all([fixed,choice]); db.session.flush()
    mango = ProductExtraOption.query.filter_by(nombre='Mango QA').one()
    db.session.add_all([
        ComboItem(combo_id=combo.id,producto_id=scope['product_id'],combo_group_id=fixed.id,cantidad=1,activo=True,es_seleccionable=False,fixed_flavor_option_id=mango.id),
        ComboItem(combo_id=combo.id,producto_id=drink.id,combo_group_id=choice.id,cantidad=1,activo=True,es_seleccionable=True,grupo_seleccion='Bebidas',max_selecciones=2),
    ])
    db.session.commit()
    Path('/tmp/parcerito-flow-qa-combo').write_text(str(combo.id))
app.config.update(WTF_CSRF_ENABLED=True, SESSION_COOKIE_SECURE=False)

def capture_otp(phone, message, **kwargs):
    code = re.search(r'\*(\d{6})\*', message)
    assert phone == '+34600000000' and code
    Path('/tmp/parcerito-flow-qa-otp').write_text(code[1])
    return True

if __name__ == '__main__':
    with patch('services.enviar_whatsapp_generico', side_effect=capture_otp):
        app.run(host='127.0.0.1', port=5079, debug=False, use_reloader=False, threaded=False)
