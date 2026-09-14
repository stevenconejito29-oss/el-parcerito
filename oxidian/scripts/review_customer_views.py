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
