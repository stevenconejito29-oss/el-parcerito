import io
import unittest
from unittest.mock import patch
from flask import Flask
from werkzeug.datastructures import MultiDict
from extensions import db, login_manager
from models import ComboGroup, ComboItem, MenuConfig, Product, ProductExtraGroup, ProductExtraOption, User
from combo_builder import build_combo
from combo_form_parser import ComponenteInput, ComboParseError, parse_componentes
from routes.admin import admin_bp


class CatalogPolishTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite://', WTF_CSRF_ENABLED=False, SESSION_PROTECTION=None)
        db.init_app(self.app); login_manager.init_app(self.app)
        login_manager.user_loader(lambda uid: db.session.get(User, int(uid)))
        self.app.register_blueprint(admin_bp, url_prefix='/admin')
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(nombre='QA', email='qa@example.invalid', rol='super_admin', activo=True, password_hash='!')
        self.product = Product(nombre='Base', precio=5, activo=True)
        self.combo = Product(nombre='Combo', precio=10, activo=True, es_combo=True)
        db.session.add_all([self.user, self.product, self.combo]); db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s['_user_id'] = str(self.user.id); s['_fresh'] = True

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def component(self, **kwargs):
        return ComponenteInput(producto_id=self.product.id, producto=self.product, cantidad=1, es_seleccionable=False, grupo=None, max_selecciones=1, **kwargs)

    def test_rebuild_removes_old_flavor_relationships(self):
        group = ProductExtraGroup(producto_id=self.product.id, nombre='Sabores', tipo='sabor')
        db.session.add(group); db.session.flush()
        option = ProductExtraOption(grupo_id=group.id, nombre='Opción')
        db.session.add(option); db.session.flush()
        build_combo(self.combo, [self.component(allowed_flavor_option_ids=[option.id])])
        db.session.commit()
        build_combo(self.combo, [self.component()]); db.session.commit()
        self.assertEqual(ComboItem.query.count(), 1)
        self.assertEqual(ComboGroup.query.count(), 1)
        self.assertEqual(ComboItem.query.one().allowed_flavor_options, [])
        table = db.metadata.tables['combo_item_allowed_flavors']
        self.assertEqual(db.session.execute(db.select(table)).all(), [])
        self.assertIsNotNone(db.session.get(ProductExtraOption, option.id))

    def test_unused_group_not_created(self):
        build_combo(self.combo, [self.component()], group_defs={'unused': {'tipo':'seleccion', 'nombre':'Vacío'}})
        db.session.commit()
        self.assertEqual(ComboGroup.query.count(), 1)
        self.assertFalse(ComboGroup.query.one().es_seleccion)

    def form(self, **kwargs):
        values = dict(comp_prod_id=str(self.product.id), comp_cantidad='1', comp_tipo='fijo', comp_grupo='', comp_max_sel='1')
        values.update(kwargs)
        return MultiDict(values)

    def test_unknown_group_and_invalid_product_id_rejected(self):
        for form in (self.form(comp_group_uid='missing'), self.form(comp_prod_id='broken')):
            with self.assertRaises(ComboParseError):
                parse_componentes(form, productos_permitidos={self.product.id:self.product})

    def test_incompatible_delivery_rejected(self):
        self.product.modalidad_entrega = 'recogida'
        with self.assertRaisesRegex(ComboParseError, 'modalidades'):
            parse_componentes(self.form(), productos_permitidos={self.product.id:self.product}, parent_delivery_mode='ambas')
        with self.assertRaisesRegex(ComboParseError, 'tipo de entrega'):
            parse_componentes(self.form(), productos_permitidos={self.product.id:self.product}, parent_delivery_type='programado')

    @patch('routes.admin.delete_image')
    @patch('routes.admin.save_image', return_value=None)
    def test_failed_upload_does_not_delete_existing_image(self, save, delete):
        response = self.client.post('/admin/menu-config/crear', data={
            'tipo':'banner', 'pagina':'home', 'imagen_url':'banners/existing.jpg',
            'titulo':'x'*161, 'imagen_archivo':(io.BytesIO(b'invalid'), 'test.jpg')})
        self.assertEqual(response.status_code, 302)
        delete.assert_not_called()
        self.assertEqual(MenuConfig.query.count(), 0)

    @patch('routes.admin.delete_image')
    def test_deleting_banner_keeps_shared_image(self, delete):
        items = [MenuConfig(tipo='banner', imagen_url='banners/shared.jpg') for _ in range(2)]
        db.session.add_all(items); db.session.commit()
        self.client.post(f'/admin/menu-config/{items[0].id}/eliminar')
        delete.assert_not_called()
        self.assertEqual(MenuConfig.query.count(), 1)

    def test_register_and_block_customer(self):
        self.client.post('/admin/clientes/registrar', data={'nombre':'Cliente QA','telefono':'+34600000000'})
        customer = User.query.filter_by(rol='cliente').one()
        self.client.post(f'/admin/clientes/{customer.id}/acceso', data={'activo':'0'})
        self.assertFalse(customer.activo)
        self.assertEqual(customer.mfa_session_version, 1)
        self.client.post('/admin/clientes/registrar', data={'nombre':'Duplicado','telefono':'+34600000000'})
        self.assertEqual(User.query.filter_by(rol='cliente').count(), 1)

    def test_existing_combo_audit_detects_foreign_group(self):
        from combo_audit import audit_combo
        foreign = ComboGroup(combo_id=self.product.id, nombre='Ajeno', tipo='fijo')
        db.session.add(foreign); db.session.flush()
        item = ComboItem(combo_id=self.combo.id, producto_id=self.product.id, cantidad=1, combo_group_id=foreign.id)
        db.session.add(item); db.session.commit()
        self.assertTrue(any('grupo propio' in issue for issue in audit_combo(self.combo, [item], [])))

    def test_duplicate_group_names_cannot_split_public_selection(self):
        other = Product(nombre='Otra base', precio=5, activo=True)
        db.session.add(other); db.session.flush()
        form = MultiDict([
            ('comp_prod_id', str(self.product.id)), ('comp_prod_id', str(other.id)),
            ('comp_cantidad','1'), ('comp_cantidad','1'),
            ('comp_tipo','sel'), ('comp_tipo','sel'),
            ('comp_grupo','Bebida'), ('comp_grupo','bebida'),
            ('comp_max_sel','1'), ('comp_max_sel','1'),
            ('comp_group_uid','a'), ('comp_group_uid','b'),
        ])
        groups = {key:dict(tipo='seleccion', nombre=name, max_selecciones=1) for key,name in [('a','Bebida'),('b','bebida')]}
        with self.assertRaisesRegex(ComboParseError, 'nombres distintos'):
            parse_componentes(form, productos_permitidos={self.product.id:self.product, other.id:other}, group_defs=groups)

    def test_zero_and_nonnumeric_quantities_are_not_changed_to_one(self):
        for quantity in ('0', 'invalid'):
            with self.assertRaises(ComboParseError):
                parse_componentes(self.form(comp_cantidad=quantity), productos_permitidos={self.product.id:self.product})
