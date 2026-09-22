import os
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
from models import User, Categoria, Product, Order, OrderItem, SiteConfig, ZonaEntrega, utcnow
app = create_app('development')
app.config['SESSION_PROTECTION'] = None
with app.app_context():
    db.create_all()
    SiteConfig.set('NOMBRE_NEGOCIO', 'El Parcerito · QA', descripcion='QA')
    SiteConfig.set('FEATURE_DELIVERY', '1', descripcion='QA')
    SiteConfig.set('FEATURE_RECOGIDA', '1', descripcion='QA')
    SiteConfig.set('DIRECCION_NEGOCIO', 'Calle de prueba 12', descripcion='QA')
    SiteConfig.set('TIENDA_FORZAR_ABIERTA', '1', descripcion='QA')
    SiteConfig.set('TIENDA_FORZAR_CERRADA', '0', descripcion='QA')
    SiteConfig.set('DELIVERY_MODO', 'inmediato', descripcion='QA')
    db.session.add(ZonaEntrega(nombre='Zona QA', precio_envio=2, activo=True))
    users = {}
    for role in ['admin','cocina','preparacion','repartidor','cliente']:
        u = User(nombre='QA '+role, email=role+'@test.invalid', rol=role, activo=True, en_linea=True, last_seen=utcnow())
        u.set_password('qa-only-password')
        db.session.add(u)
        db.session.flush()
        users[role] = u.id
    from models import AdminFeature
    db.session.add(AdminFeature(user_id=users["admin"], feature="pos", activo=True))
    cat = Categoria(nombre='Prueba', activo=True)
    db.session.add(cat); db.session.flush()
    product = Product(nombre='Producto de prueba', precio=5, categoria_id=cat.id, activo=True, canal_preparacion='cocina')
    db.session.add(product); db.session.flush()
    for index, state in enumerate(['pendiente','armando','listo','en_ruta'], 1):
        order = Order(numero_pedido=f'QA-{index}', cliente_id=users['cliente'], estado=state, total=10, subtotal=10, metodo_pago='efectivo', tipo_entrega_cliente='delivery', direccion_entrega='Dirección de prueba', preparador_id=users['cocina'], repartidor_id=users['repartidor'] if state=='en_ruta' else None)
        db.session.add(order); db.session.flush()
        db.session.add(OrderItem(pedido_id=order.id, producto_id=product.id, cantidad=2, precio_unit=5, subtotal=10))
    for name in ['Arepa con queso', 'Empanadas de carne y ají de la casa', 'Bandeja para compartir', 'Jugo natural', 'Producto con un nombre especialmente largo para verificar la lectura']:
        db.session.add(Product(nombre=name, precio=5, categoria_id=cat.id, activo=True, canal_preparacion='cocina'))
    product_id = product.id
    db.session.commit()

out=Path('/tmp/parcerito-customer-pages'); out.mkdir(exist_ok=True)
client=app.test_client()
with client.session_transaction() as session:
    session['carrito']={str(product_id):2}
    session['guest_order_tokens']={'1': 'qa-only-order-token'}
for name,route in [('menu','/'),('carrito','/carrito'),('chat','/ayuda'),('checkout','/checkout')]:
    response=client.get(route,follow_redirects=True)
    assert response.status_code == 200, (name,response.status_code)
    (out/(name+'.html')).write_bytes(response.data)
    print(name,response.status_code,len(response.data),response.request.path)

for name,route in [('producto',f'/producto/{product_id}'),('club','/club'),('legal','/informacion-legal'),('seguimiento','/pedido/1/confirmado'),('franjas','/delivery/preview')]:
    response=client.get(route,follow_redirects=True)
    assert response.status_code == 200, (name,response.status_code)
    (out/(name+'.html')).write_bytes(response.data)
    print(name,response.status_code,response.request.path)
with client.session_transaction() as session:
    session['carrito']={}
response=client.get('/carrito')
(out/'vacio.html').write_bytes(response.data)

# Seguimiento específico de recogida, sin campos heredados del reparto.
with app.app_context():
    order = db.session.get(Order, 2)
    order.tipo_entrega_cliente = 'recogida'
    order.estado = 'armando'
    order.confirmacion_estado = 'confirmed'
    order.direccion_entrega = None
    order.repartidor_id = None
    db.session.commit()
with client.session_transaction() as session:
    session['guest_order_tokens'] = {'2': 'qa-pickup-private-token'}
response = client.get('/pedido/2/confirmado')
assert response.status_code == 200
assert b'qa-pickup-private-token' not in response.data
(out/'recogida.html').write_bytes(response.data)

# Combo con selección múltiple: comprueba menú, cantidades y desglose del carrito.
with app.app_context():
    from models import ComboItem, ComboGroup, Stock
    base = db.session.get(Product, product_id)
    combo = Product(nombre='Combo para compartir', precio=16, categoria_id=base.categoria_id, activo=True, es_combo=True)
    drink = Product(nombre='Bebida de prueba', precio=2, categoria_id=base.categoria_id, activo=True)
    db.session.add_all([combo, drink]); db.session.flush()
    db.session.add_all([Stock(producto_id=base.id,cantidad=100),Stock(producto_id=drink.id,cantidad=100)])
    fixed = ComboGroup(combo_id=combo.id,nombre='Incluido',tipo='fijo')
    choice = ComboGroup(combo_id=combo.id,nombre='Bebidas',tipo='seleccion',min_selecciones=3,max_selecciones=3)
    db.session.add_all([fixed,choice]);db.session.flush()
    a=ComboItem(combo_id=combo.id,producto_id=base.id,combo_group_id=fixed.id,cantidad=2,activo=True,es_seleccionable=False)
    b=ComboItem(combo_id=combo.id,producto_id=drink.id,combo_group_id=choice.id,cantidad=1,activo=True,es_seleccionable=True,grupo_seleccion='Bebidas',max_selecciones=3)
    db.session.add_all([a,b]);db.session.commit()
    combo_id,choice_id=combo.id,b.id
response=client.get(f'/producto/{combo_id}')
assert response.status_code == 200
(out/'combo.html').write_bytes(response.data)
response=client.post(f'/carrito/agregar/{combo_id}', data={'cantidad':'2',f'combo_item_qty_{choice_id}':'3'})
assert response.status_code in (302,303), response.status_code
response=client.get('/carrito')
assert response.status_code == 200
assert 'Combo para compartir' in response.text and '3×' in response.text, 'Se perdió la selección del combo'
(out/'combo_carrito.html').write_bytes(response.data)

# Cierre, preapertura y acceso privado con textos extensos, solo datos QA.
with app.app_context():
    SiteConfig.set('TIENDA_FORZAR_CERRADA','1')
    SiteConfig.set('TIENDA_FORZAR_ABIERTA','0')
    SiteConfig.set('TIENDA_MENSAJE_CIERRE', 'Hoy estamos cerrados por mantenimiento. Volvemos con nuestro horario habitual; tu selección quedará guardada para continuar cuando abramos. ' * 3)
    db.session.get(Product,combo_id).nombre='Combo familiar con empanadas, bebidas y opciones para compartir en una ocasión especial'
    db.session.commit()
for name,route in [('cerrado_menu','/'),('cerrado_producto',f'/producto/{product_id}'),('cerrado_combo',f'/producto/{combo_id}'),('cerrado_carrito','/carrito'),('cerrado_checkout','/checkout')]:
    response=client.get(route, follow_redirects=True)
    assert response.status_code==200
    (out/(name+'.html')).write_bytes(response.data)
with app.app_context():
    SiteConfig.set('PREAPERTURA_ACTIVA','1')
    SiteConfig.set('PREAPERTURA_TITULO','Estamos preparando una nueva experiencia para compartir contigo y toda tu familia')
    SiteConfig.set('PREAPERTURA_MENSAJE','Pronto volveremos a recibir tus pedidos. Consulta nuestros horarios y novedades; gracias por acompañarnos. ' * 4)
    db.session.commit()
response=client.get('/')
assert 'launch-card' in response.text
(out/'preapertura.html').write_bytes(response.data)
with app.app_context():
    from models import CustomerAccessGrant
    owner=User(nombre='Owner QA',email='private-owner@qa.invalid',password_hash='!',rol='super_admin',activo=True)
    db.session.add(owner);db.session.flush()
    customer=db.session.get(User,users['cliente'])
    customer.telefono='+34600000000';customer.telefono_normalizado=customer.telefono
    db.session.add(CustomerAccessGrant(user_id=customer.id,approved_by=owner.id,activo=True))
    code=customer.generar_cod_puntos()
    SiteConfig.set('ACCESO_CLIENTES_REGISTRADOS','1');db.session.commit()
    identity_id=customer.id
private_client=app.test_client()
response=private_client.get('/acceso')
assert response.status_code==200 and 'name="telefono"' in response.text, 'La preapertura intercepta el acceso'
assert 'rel="manifest"' not in response.text
assert private_client.get('/manifest.webmanifest').status_code==403
with private_client.session_transaction() as session:
    import time
    session['customer_access_pending']={'id':identity_id,'phone':'+34600000000','at':time.time()}
response=private_client.post('/acceso',data={'action':'verify','codigo':code})
assert response.status_code==303 and response.location=='/'
assert private_client.get('/manifest.webmanifest').status_code==200
with app.app_context():
    SiteConfig.set('PREAPERTURA_ACTIVA','0');db.session.commit()
assert 'name="ox-push-eligible" content="1"' in private_client.get('/').text
print('Acceso privado + preapertura + OTP + manifiesto PWA: OK')
