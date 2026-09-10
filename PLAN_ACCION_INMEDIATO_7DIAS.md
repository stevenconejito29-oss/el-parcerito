# 🎯 PLAN DE ACCIÓN INMEDIATO
## Próximos 7 días - Fase 1: Estabilización

---

## DÍA 1-2: AUDITORÍA Y DIAGNÓSTICO

### ✅ Tareas Concretas

#### 1. Crear Issues de Problemas (GitHub/Notion)
```
[ ] Problema 1: Chatbot - Menú super admin no completo
    - Descripción: Comandos /menu, /admin no responden correctamente
    - Prioridad: CRÍTICA
    - Archivos: /chat/bot.js
    - Assignee: [TÚ]

[ ] Problema 2: Combos - Errores en validación
    - Descripción: Combos con selección múltiple fallan
    - Prioridad: CRÍTICA
    - Archivos: /oxidian/models.py, /routes/public.py
    - Assignee: [TÚ]

[ ] Problema 3: PWA - Transiciones lentas
    - Descripción: Al navegar entre secciones hay tirones
    - Prioridad: ALTA
    - Archivos: /static/js/*, /static/css/*
    - Assignee: [TÚ]

[ ] Problema 4: Stock mezclado
    - Descripción: Clientes no ven claramente qué bar tiene qué
    - Prioridad: ALTA
    - Archivos: /routes/public.py, /templates/catalogo.html
    - Assignee: [TÚ]

[ ] Problema 5: Datos hardcodeados
    - Descripción: Logo, nombre, horarios no usan config
    - Prioridad: MEDIA
    - Archivos: /routes/public.py, /templates/*.html
    - Assignee: [TÚ]
```

#### 2. Ejecutar Auditoría Técnica
```bash
# Terminal: En /home/panzeta/Documentos/el-parcerito

# 1. Contar líneas de código
wc -l oxidian/**/*.py oxidian/static/js/*.js

# 2. Listar todos los endpoints API
grep -r "@.*route" oxidian/routes/ | grep -v ".pyc"

# 3. Listar todas las consultas SELECT en bot.js
grep -r "SELECT\|query\|find\|exec" chat/bot.js | head -20

# 4. Listar archivos CSS/JS
ls -lh oxidian/static/css/
ls -lh oxidian/static/js/

# 5. Ver tamaño de BD
du -sh oxidian/ chat/
```

#### 3. Crear Documento de Estado Actual
```markdown
# Estado Actual del Sistema (Checklist)

## Backend (Flask)
- [ ] ¿Todos los endpoints responden?
- [ ] ¿Hay errores en logs? (python -m flask run)
- [ ] ¿Queries están optimizadas?
- [ ] ¿Rate limiting funciona?

## Frontend (PWA)
- [ ] ¿Service Worker funciona?
- [ ] ¿Lighthouse score es? (>85 = OK)
- [ ] ¿Carga es rápida? (<2s = OK)
- [ ] ¿Responsive en móvil?

## Chatbot (Node.js)
- [ ] ¿Bot conecta con Evolution API?
- [ ] ¿Recibe mensajes cliente?
- [ ] ¿Responde menú admin?
- [ ] ¿Responde menú super admin?

## Base de Datos
- [ ] ¿Hay datos de prueba?
- [ ] ¿Índices están creados?
- [ ] ¿Performance OK?

## Configuración
- [ ] ¿.env tiene todas las keys?
- [ ] ¿Logo/nombre están en config?
- [ ] ¿Horarios están en config?
```

---

## DÍA 3-4: CORRECCIONES FASE 1

### ✅ Fix 1: Habilitar Configuración Dinámica

**Archivo:** `/home/panzeta/Documentos/el-parcerito/oxidian/models.py`

```python
# Buscar: class SiteConfig
# Agregar método de validación:

class SiteConfig(db.Model):
    # ... existing fields ...
    
    @classmethod
    def validar_configuracion_critica(cls):
        """Valida que todas las configs críticas estén presentes"""
        critica = {
            'nombre_negocio': 'El Parcerito',
            'logo_url': '/static/img/logo.png',
            'whatsapp_country_code': '34',  # España
            'moneda': 'USD',
            'timezone': 'America/Caracas',
            'hora_apertura': '09:00',
            'hora_cierre': '23:00',
            'email_contacto': 'info@parcerito.com',
            'color_primario': '#FF6B35',
            'color_secundario': '#004E89',
        }
        
        faltantes = []
        for key, default in critica.items():
            valor = cls.get(key)
            if valor is None:
                cls.set(key, default)
                faltantes.append(f"✓ Configurado {key} = {default}")
        
        if faltantes:
            print(f"[CONFIG] Configuraciones faltantes establecidas:\n" + 
                  "\n".join(faltantes))
        
        return len(faltantes) == 0
```

**Archivo:** `/home/panzeta/Documentos/el-parcerito/oxidian/app.py`

```python
# En la función init_app() o al inicio de main:

# Agregar después de db.create_all():
print("[STARTUP] Validando configuración del sistema...")
SiteConfig.validar_configuracion_critica()
print("[STARTUP] ✓ Configuración OK")
```

**Verification:**
```bash
cd /home/panzeta/Documentos/el-parcerito
python -m flask shell

# En shell:
from models import SiteConfig
SiteConfig.validar_configuracion_critica()
# Debe imprimir: [CONFIG] Configuraciones faltantes establecidas...

# Ver valores:
SiteConfig.get('nombre_negocio')
SiteConfig.get('logo_url')
```

---

### ✅ Fix 2: Template Base - Usar Configuración Dinámica

**Archivo:** `/home/panzeta/Documentos/el-parcerito/oxidian/templates/base.html`

```html
<!-- Al inicio del archivo <head> -->
<meta property="og:title" content="{{ config_nombre_negocio }}">
<meta property="og:description" content="Tienda online de {{ config_nombre_negocio }}">
<meta property="og:image" content="{{ config_logo_url }}">

<!-- En el <body> -->
<div class="navbar">
  <div class="navbar-brand">
    <img src="{{ config_logo_url }}" alt="{{ config_nombre_negocio }}" class="logo">
    <span class="business-name">{{ config_nombre_negocio }}</span>
  </div>
</div>

<footer>
  <p>&copy; 2026 {{ config_nombre_negocio }}</p>
  <p>Horario: {{ config_hora_apertura }} - {{ config_hora_cierre }}</p>
  <a href="https://wa.me/{{ config_whatsapp_number }}">WhatsApp</a>
</footer>
```

**Archivo:** `/home/panzeta/Documentos/el-parcerito/oxidian/routes/public.py`

```python
# En la función que renderiza template:

@public_bp.before_request
def inject_config():
    """Inyectar config en todos los templates"""
    from models import SiteConfig
    return {
        'config_nombre_negocio': SiteConfig.get('nombre_negocio', 'El Parcerito'),
        'config_logo_url': SiteConfig.get('logo_url', '/static/img/logo.png'),
        'config_hora_apertura': SiteConfig.get('hora_apertura', '09:00'),
        'config_hora_cierre': SiteConfig.get('hora_cierre', '23:00'),
        'config_whatsapp_number': SiteConfig.get('whatsapp_number', ''),
        'config_email': SiteConfig.get('email_contacto', ''),
        'config_color_primario': SiteConfig.get('color_primario', '#FF6B35'),
        'config_color_secundario': SiteConfig.get('color_secundario', '#004E89'),
    }
```

---

### ✅ Fix 3: Validar Combos Correctamente

**Archivo:** `/home/panzeta/Documentos/el-parcerito/oxidian/models.py`

Agregar después de la clase `Product`:

```python
class ComboValidation:
    """Validación de estructura y precio de combos"""
    
    @staticmethod
    def validar_estructura(combo):
        """Retorna (es_válido, lista_errores)"""
        if not combo.es_combo:
            return True, []
        
        errores = []
        
        # Validar que tiene grupos
        grupos = list(combo.combo_groups)
        if not grupos:
            errores.append("Combo sin grupos de selección")
            return False, errores
        
        # Validar cada grupo
        for grupo in grupos:
            items = list(grupo.combo_items)
            if not items:
                errores.append(f"Grupo '{grupo.nombre}' sin items")
            
            if grupo.tipo == 'seleccion':
                if not grupo.max_selecciones:
                    errores.append(f"Grupo '{grupo.nombre}' sin max_selecciones")
                if grupo.max_selecciones < 1:
                    errores.append(f"Grupo '{grupo.nombre}' max_selecciones < 1")
        
        # Validar precio
        if combo.combo_precio_modo == 'fijo':
            if not combo.combo_precio_base or combo.combo_precio_base <= 0:
                errores.append("Combo precio fijo inválido")
        elif combo.combo_precio_modo == 'descuento_porcentaje':
            if combo.combo_descuento_pct < 0 or combo.combo_descuento_pct > 100:
                errores.append("Descuento porcentaje fuera de rango [0-100]")
        
        return len(errores) == 0, errores
    
    @staticmethod
    def validar_selecciones_cliente(combo_id, selecciones_dict):
        """
        Valida que selecciones del cliente son válidas
        
        selecciones_dict = {
            'grupo_1': [item_1, item_2],  # Múltiple
            'grupo_2': item_3             # Única
        }
        
        Retorna: (es_válido, precio_final, errores)
        """
        combo = Product.query.get(combo_id)
        if not combo or not combo.es_combo:
            return False, None, ["No es un combo válido"]
        
        válido, errores = ComboValidation.validar_estructura(combo)
        if not válido:
            return False, None, errores
        
        precio_total = Decimal('0')
        
        for grupo in combo.combo_groups:
            grupo_id_str = f"grupo_{grupo.id}"
            selecciones = selecciones_dict.get(grupo_id_str, [])
            
            # Normalizar a lista
            if not isinstance(selecciones, list):
                selecciones = [selecciones] if selecciones else []
            
            if grupo.tipo == 'fijo':
                # Agregar todos los items
                for item in grupo.combo_items:
                    precio_total += item.precio_extra or Decimal('0')
            
            elif grupo.tipo == 'seleccion':
                # Validar cantidad
                if len(selecciones) < grupo.min_selecciones:
                    errores.append(f"{grupo.nombre}: mínimo {grupo.min_selecciones}")
                if len(selecciones) > grupo.max_selecciones:
                    errores.append(f"{grupo.nombre}: máximo {grupo.max_selecciones}")
                
                # Validar items y agregar precio
                items_validos = {item.id for item in grupo.combo_items}
                for item_id in selecciones:
                    if item_id not in items_validos:
                        errores.append(f"Item {item_id} inválido para {grupo.nombre}")
                    else:
                        item = ComboItem.query.get(item_id)
                        precio_total += item.precio_extra or Decimal('0')
        
        if errores:
            return False, None, errores
        
        # Calcular precio final
        if combo.combo_precio_modo == 'fijo':
            precio_final = combo.combo_precio_base
        elif combo.combo_precio_modo == 'descuento_porcentaje':
            descuento = precio_total * (combo.combo_descuento_pct / 100)
            precio_final = precio_total - descuento
        else:  # sum
            precio_final = precio_total
        
        return True, precio_final, []
```

**Test:**
```python
# En python shell:
from models import Product, ComboValidation

combo = Product.query.filter_by(es_combo=True).first()

# Validar estructura
válido, errores = ComboValidation.validar_estructura(combo)
print(f"Estructura válida: {válido}")
print(f"Errores: {errores}")

# Validar selecciones cliente
selecciones = {'grupo_1': [1, 2], 'grupo_2': 5}
válido, precio, errores = ComboValidation.validar_selecciones_cliente(combo.id, selecciones)
print(f"Selecciones válidas: {válido}")
print(f"Precio: {precio}")
```

---

### ✅ Fix 4: API - Validar Delivery/Recogida

**Archivo:** `/home/panzeta/Documentos/el-parcerito/oxidian/routes/public.py`

```python
def validar_modalidad_entrega(carrito_dict, tipo_entrega):
    """
    Valida que TODOS los items del carrito soportan el tipo_entrega
    
    Retorna: (válido: bool, razón: str, productos_problema: list)
    """
    problemas = []
    
    for prod_id, qty in carrito_dict.items():
        prod = Product.query.get(prod_id)
        if not prod:
            continue
        
        # Validar modalidad
        if prod.modalidad_entrega in ['ambas', tipo_entrega]:
            continue  # OK
        
        # Problema
        problemas.append({
            'id': prod_id,
            'nombre': prod.nombre,
            'modalidad': prod.modalidad_entrega,
            'razón': f"Solo disponible para {prod.modalidad_entrega}"
        })
    
    if problemas:
        return False, f"{len(problemas)} producto(s) no disponibles", problemas
    
    return True, 'OK', []

@public_bp.route('/api/checkout/validar-modalidad', methods=['POST'])
@limiter.limit("60/minute")
def validar_modalidad():
    """
    POST /api/checkout/validar-modalidad
    {
      "carrito": {"prod_id": qty, ...},
      "tipo_entrega": "delivery" | "recogida"
    }
    """
    data = request.get_json() or {}
    carrito = data.get('carrito', {})
    tipo_entrega = data.get('tipo_entrega', 'delivery')
    
    if tipo_entrega not in ['delivery', 'recogida']:
        return _json_no_store({
            'ok': False,
            'error': 'tipo_entrega inválido'
        }, 400)
    
    válido, razón, problemas = validar_modalidad_entrega(carrito, tipo_entrega)
    
    return _json_no_store({
        'ok': válido,
        'razón': razón,
        'productos_problema': problemas
    })

# En la ruta de checkout actual: Agregar validación
@public_bp.route('/checkout/crear', methods=['POST'])
def checkout_crear():
    # ... código existente ...
    
    # Validar modalidad ANTES de crear orden
    tipo_entrega = request.form.get('tipo_entrega', 'delivery')
    carrito_dict = session.get('carrito', {})
    
    válido, razón, problemas = validar_modalidad_entrega(carrito_dict, tipo_entrega)
    if not válido:
        flash(f"❌ Error: {razón}", 'error')
        for prod in problemas:
            flash(f"  • {prod['nombre']}: {prod['razón']}", 'error')
        return redirect(url_for('public.carrito'))
    
    # ... continuar con creación de orden ...
```

---

## DÍA 5: TESTING Y VALIDACIÓN

### ✅ Checklist de Testing

```bash
# 1. Configuración dinámica
curl -s http://localhost:5000/api/config | jq .

# 2. Endpoint validar modalidad
curl -s -X POST http://localhost:5000/api/checkout/validar-modalidad \
  -H "Content-Type: application/json" \
  -d '{
    "carrito": {"1": 1, "2": 2},
    "tipo_entrega": "delivery"
  }' | jq .

# 3. Bot: Enviar mensaje de prueba
# (a través de Evolution API o WhatsApp)

# 4. PWA: Abrir en navegador
# http://localhost:5000
# Verificar:
# - [ ] Logo y nombre correctos
# - [ ] Productos cargados
# - [ ] Combos válidos
# - [ ] Carrito funciona

# 5. Database: Verificar datos
python -m flask shell
>>> from models import SiteConfig
>>> SiteConfig.get_all()
```

---

## DÍA 6-7: DOCUMENTACIÓN Y PREPARACIÓN FASE 2

### ✅ Crear Documentos Base

**Archivo:** `/home/panzeta/Documentos/ARQUITECTURA_SISTEMA.md`
```markdown
# Arquitectura del Sistema El Parcerito

[Documentar estructura actual]

## Bases de Datos
[Tablas principales]

## API Endpoints
[Listar todos]

## Flujos Principales
[Compra, pedido, puntos]
```

**Archivo:** `/home/panzeta/Documentos/GUIA_ADMIN.md`
```markdown
# Guía de Administración

## Configurar Tienda (Super Admin)
1. Ingresar a /superadmin
2. Ir a "Configuración"
3. ...

## Gestionar Bares (Super Admin)
...

## Gestionar Inventario (Admin Bar)
...
```

### ✅ Preparar Fase 2: Stock Separado

```bash
# Revisar código existente de stock
grep -n "proveedor_id\|Stock\|Proveedor" oxidian/models.py | head -30

# Crear rama de desarrollo
cd /home/panzeta/Documentos/el-parcerito
git checkout -b feature/separacion-stock
```

---

## 📝 ENTREGABLES DESPUÉS DE DÍA 7

1. ✅ Sistema sin errores críticos
2. ✅ Configuración dinámica funcionando
3. ✅ Combos validados correctamente
4. ✅ Modalidad entrega controlada
5. ✅ Documentación básica
6. ✅ Rama para Fase 2 lista

---

## 🔄 PRÓXIMO: Fase 2 (Separación de Stock)

La Fase 2 comenzará cuando Fase 1 esté 100% completada y testeada.

Contacte para cualquier duda durante implementación.

