"""Renderiza roles con SQLite en memoria; nunca usa datos del servidor."""
import os
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DATABASE_URL'] = 'postgresql://test:test@127.0.0.1:15432/test'
os.environ['OXIDIAN_SKIP_STARTUP_DB'] = '1'
os.environ['OXIDIAN_MFA_ENFORCED'] = '0'
from config import DevelopmentConfig
DevelopmentConfig.SQLALCHEMY_DATABASE_URI = 'sqlite://'
DevelopmentConfig.TESTING = True
DevelopmentConfig.WTF_CSRF_ENABLED = False
from app import create_app
from extensions import db
from models import User, Categoria, Product, Order, OrderItem, SiteConfig, utcnow
app = create_app('development')
app.config['SESSION_PROTECTION'] = None
with app.app_context():
    db.create_all()
    SiteConfig.set('NOMBRE_NEGOCIO', 'El Parcerito · QA', descripcion='QA')
    SiteConfig.set('FEATURE_DELIVERY', '1', descripcion='QA')
    SiteConfig.set('TIENDA_FORZAR_ABIERTA', '1', descripcion='QA')
    SiteConfig.set('TIENDA_FORZAR_CERRADA', '0', descripcion='QA')
    SiteConfig.set('DELIVERY_MODO', 'inmediato', descripcion='QA')
    users = {}
    for role in ['super_admin','admin','cocina','preparacion','repartidor','cliente']:
        u = User(nombre='QA '+role, email=role+'@test.invalid', rol=role, activo=True, en_linea=True, last_seen=utcnow())
        u.set_password('qa-only-password')
        db.session.add(u)
        db.session.flush()
        users[role] = u.id
    from models import AdminFeature
    AdminFeature.inicializar_para_admin(users["admin"], activar_todos=True)
    cat = Categoria(nombre='Prueba', activo=True)
    db.session.add(cat); db.session.flush()
    product = Product(nombre='Producto de prueba con un nombre largo para comprobar el trabajo diario', precio=5, categoria_id=cat.id, activo=True, canal_preparacion='cocina')
    db.session.add(product); db.session.flush()
    for index, state in enumerate(['pendiente','armando','listo','en_ruta'], 1):
        order = Order(numero_pedido=f'QA-{index}', cliente_id=users['cliente'], estado=state, total=10, subtotal=10, metodo_pago='efectivo', tipo_entrega_cliente='delivery', direccion_entrega='Dirección de prueba', preparador_id=users['cocina'], repartidor_id=users['repartidor'] if state=='en_ruta' else None)
        db.session.add(order); db.session.flush()
        db.session.add(OrderItem(pedido_id=order.id, producto_id=product.id, cantidad=2, precio_unit=5, subtotal=10))
    pickup_order = Order(numero_pedido='QA-REC', cliente_id=users['cliente'], estado='listo', total=10, subtotal=10, metodo_pago='efectivo', tipo_entrega_cliente='recogida', preparador_id=users['cocina'])
    db.session.add(pickup_order); db.session.flush()
    db.session.add(OrderItem(pedido_id=pickup_order.id, producto_id=product.id, cantidad=2, precio_unit=5, subtotal=10))
    product_id = product.id
    db.session.commit()

with app.app_context():
    from business_time import business_today
    scheduled = Product(nombre='Encargo de prueba para preparar y recoger', precio=10, activo=True,
                        tipo_entrega='programado', fecha_llegada=business_today())
    db.session.add(scheduled); db.session.flush()
    for index,state in enumerate(['pendiente','armando','listo'], 10):
        order=Order(numero_pedido=f'QA-{index}',cliente_id=users['cliente'],estado=state,
                    total=10,subtotal=10,metodo_pago='efectivo',tipo_entrega_cliente='recogida',
                    preparador_id=users['preparacion'])
        db.session.add(order); db.session.flush()
        db.session.add(OrderItem(pedido_id=order.id,producto_id=scheduled.id,cantidad=1,precio_unit=10,subtotal=10))
    db.session.commit()

out=Path(os.environ.get('ROLE_REVIEW_OUTPUT', '/tmp/parcerito-role-review')); out.mkdir(exist_ok=True)
views=[('superadmin','super_admin','/superadmin/dashboard'),
       ('configuracion','super_admin','/superadmin/config'),
       ('clientes','super_admin','/admin/clientes'),
       ('banners','super_admin','/admin/menu-config'),
       ('admin','admin','/admin/dashboard'),('pedidos','admin','/admin/pedidos'),
       ('productos','admin','/admin/productos'),('personal','admin','/admin/usuarios'),
       ('finanzas','admin','/admin/finanzas'),('cocina','cocina','/preparador/pedidos'),
       ('preparacion','preparacion','/preparador/pedidos'),('repartidor','repartidor','/repartidor/ruta')]
manifest=[]
for name,role,route in views:
    client=app.test_client()
    with client.session_transaction() as session:
        session['_user_id']=str(users[role]); session['_fresh']=True
    response=client.get(route,follow_redirects=True)
    assert response.status_code == 200 and response.request.path == route, (name,response.status_code,response.request.path)
    (out/(name+'.html')).write_bytes(response.data)
    manifest.append({'name':name,'role':role,'route':route})
    print(name,response.status_code,flush=True)
(out/'manifest.json').write_text(json.dumps(manifest))

# Ticket independiente: no hereda los listeners del layout de roles.
with client.session_transaction() as session:
    session['_user_id'] = str(users['cocina'])
    session['_fresh'] = True
ticket = client.get('/pos/ticket/1')
assert ticket.status_code == 200
(out/'ticket.html').write_bytes(ticket.data)

# Entrada privada: ambos pasos, sin exponer un OTP ni enviar WhatsApp.
with app.app_context():
    SiteConfig.set('ACCESO_CLIENTES_REGISTRADOS', '1')
    db.session.commit()
access_client = app.test_client()
# La gestión de puntos tiene tres pantallas independientes.
with client.session_transaction() as session:
    session['_user_id'] = str(users['super_admin']); session['_fresh'] = True
for tab in ('clientes', 'recompensas', 'historial'):
    response = client.get('/marketing/puntos?tab=' + tab)
    assert response.status_code == 200, response.status_code
    name = 'puntos_' + tab
    (out/(name+'.html')).write_bytes(response.data)
    manifest.append({'name':name, 'role':'super_admin', 'route':'/marketing/puntos?tab='+tab})

for name, code_step in [('acceso', False), ('acceso_codigo', True)]:
    with access_client.session_transaction() as session:
        session['customer_access_code_step'] = code_step
    response = access_client.get('/acceso')
    assert response.status_code == 200
    (out/(name+'.html')).write_bytes(response.data)
    manifest.append({'name':name, 'role':'cliente', 'route':'/acceso'})
(out/'manifest.json').write_text(json.dumps(manifest))

# El acceso se guarda sin modificar horarios y solo superadmin puede cambiarlo.
for role, value, expected in [('super_admin', '0', '0'), ('admin', '1', '0'), ('super_admin', '1', '1')]:
    with client.session_transaction() as session:
        session['_user_id'] = str(users[role]); session['_fresh'] = True
    response = client.post('/superadmin/config/guardar-seccion', data={
        'section': 'acceso-clientes', 'config_key': 'ACCESO_CLIENTES_REGISTRADOS',
        'ACCESO_CLIENTES_REGISTRADOS': value,
    })
    assert response.status_code == 302
    assert 'section=acceso' in response.location
    with app.app_context():
        assert SiteConfig.get('ACCESO_CLIENTES_REGISTRADOS') == expected
        assert SiteConfig.get('TIENDA_FORZAR_ABIERTA') == '1'
        assert SiteConfig.get('TIENDA_FORZAR_CERRADA') == '0'
print('Acceso independiente y permisos de superadmin: OK', flush=True)
