# Desarrollo seguro y mantenible

## Antes de modificar

1. Ejecutar `git status --short` y preservar cambios existentes.
2. Ubicar el flujo en `PROJECT_STRUCTURE.md`.
3. Buscar todas sus referencias con `rg`.
4. Confirmar el contrato actual en modelos, permisos y pruebas.

## Dónde colocar cada cambio

| Necesidad | Ubicación preferida |
|---|---|
| Campo, relación, estado o validación persistente | `oxidian/models.py` + migración compatible |
| Regla usada por más de un canal | `oxidian/services.py` o `*_service.py` |
| Endpoint o formulario | `oxidian/routes/<area>.py` |
| Permiso | `oxidian/permissions.py` y prueba de matriz |
| Configuración comercial | `store_config.py`, `config_defaults.py` o `SiteConfig` |
| Normalización telefónica | `oxidian/phone_utils.py` |
| Vista | `oxidian/templates/<area>/` |
| Estilo compartido | `oxidian/static/css/` |
| Interacción compartida | `oxidian/static/js/` |
| Diálogo WhatsApp | `chat/texts.js` y manejador correspondiente |
| Orquestación WhatsApp | `chat/bot.js`, consultando la API Flask |

No duplicar reglas comerciales en Jinja, JavaScript o el bot. Esas capas sólo
presentan estado y envían intenciones al servidor.

## Reglas contra hardcoding

- Usar `SiteConfig` para datos editables del negocio.
- Usar variables de entorno para secretos y conectividad.
- Generar URLs públicas desde la configuración, no desde dominios literales.
- Centralizar estados y roles en constantes existentes.
- Formatear y comparar teléfonos mediante `phone_utils.py`.
- No fijar textos de nicho: derivarlos de las capacidades de tienda.
- Nunca poner credenciales, PIN, teléfonos privados o datos de clientes en
  pruebas, plantillas, documentación o logs.

## Frontend y PWA

- Mantener `base.html` para público y `admin_base.html` para roles internos.
- Reutilizar componentes y clases antes de crear variantes.
- La interfaz debe funcionar con teclado, modo claro/oscuro y anchos pequeños,
  respetando `safe-area-inset-*` en dispositivos iOS.
- Una acción debe aparecer una sola vez en el área principal. La navegación
  lateral y la inferior pueden ofrecer acceso, pero no deben tapar contenido.
- Todo control visual necesita autorización real en backend.
- Si cambia un asset cacheable, comprobar el fingerprint y el service worker.

## Validación local

Desde la raíz:

```bash
cd oxidian
python3 -m compileall -q .
python3 -m unittest discover -s tests -q
cd ..
node --check chat/bot.js
(cd chat && npm test)
```

Estos comandos requieren las dependencias de `oxidian/requirements.txt` en el
entorno activo. Si se ejecutan dentro del contenedor, usar `python` cuando esa
sea la ruta disponible del intérprete.

Para cambios visuales o de rutas, añadir además:

```bash
cd oxidian
npm run build:css       # sólo si cambió la entrada Tailwind
npm run visual:audit    # requiere el entorno indicado por el script
```

Antes de producción, ejecutar `oxidian/scripts/predeploy_check.py` dentro del
entorno que contiene las variables reales. No usar valores ficticios para hacer
pasar el control.

## Criterio para limpiar código

Un archivo sólo se elimina cuando:

1. `rg` no encuentra consumidores activos;
2. no está registrado como blueprint, comando, script operativo o asset;
3. no participa en datos/migraciones compatibles;
4. las pruebas y recorridos relevantes pasan sin él;
5. el cambio queda explicado en documentación o commit.

Si alguna condición no puede demostrarse, se marca como legacy y se aísla; no
se borra. Esta regla evita romper instalaciones existentes por una limpieza
puramente estética.
