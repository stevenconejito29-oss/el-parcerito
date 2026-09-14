# Guía de trabajo del repositorio

Este archivo es la entrada rápida para personas y asistentes que modifiquen El
Parcerito. La documentación vigente comienza en [`docs/README.md`](docs/README.md).

## Límites y fuentes de verdad

- `oxidian/models.py` define datos, estados y roles.
- En pedidos confirmados, los valores del snapshot prevalecen sobre el
  catálogo actual, incluidos los vacíos explícitos. El fallback legacy solo
  corresponde cuando falta la clave; las vistas deben seguir la misma regla.
- `oxidian/services.py` contiene reglas de negocio compartidas.
- `oxidian/routes/` adapta HTTP a esas reglas; no debe duplicarlas.
- `chat/bot.js` orquesta la conversación, pero precios, stock, clientes y
  pedidos se consultan mediante `oxidian/routes/api_bot.py`.
- WhatsApp se limita a verificaciones y confirmaciones para clientes, además
  de los menús de admin y superadmin. El teléfono registrado en el perfil
  activo determina el rol; las listas del entorno no conceden acceso.
  Consultas, catálogo, carrito y atención humana pertenecen a la PWA/chat web.
  No ampliar WhatsApp a un asistente público sin una nueva instrucción expresa.
- Reparto inmediato y por franjas son modalidades separadas, con opción mixta.
  No mezclar asignación inmediata con reservas de franja ni apagar una
  modalidad dejando pedidos activos sin su flujo operativo.
- La tienda cobra al recibir o recoger, también en Bizum y tarjeta con
  datáfono. No introducir pagos anticipados ni bloquear la preparación por
  falta de cobro. La confirmación del primer pedido verifica identidad, no pago.
- `oxidian/store_config.py` y `oxidian/config_defaults.py` concentran la
  configuración editable. No introducir nombres, teléfonos, URLs, precios o
  reglas comerciales directamente en vistas o controladores.
- Los documentos antiguos en la raíz de `oxidian/` son referencias históricas,
  no una especificación vigente.

## Cambios seguros

1. Leer `docs/PROJECT_STRUCTURE.md` y localizar la capa responsable.
2. Revisar `git status`; el árbol puede contener trabajo legítimo sin commit.
3. Hacer cambios pequeños y conservar compatibilidad de rutas y datos.
4. Añadir o actualizar pruebas del flujo afectado.
5. Ejecutar las validaciones descritas en `docs/DEVELOPMENT.md`.
6. No borrar modelos, columnas, rutas legacy ni migraciones sólo porque no
   aparezcan en la interfaz. Antes hay que demostrar que no existen datos o
   integraciones que dependan de ellos.

## Convenciones esenciales

- Código y nombres técnicos: seguir el estilo existente; documentación y UX:
  español claro.
- Teléfonos: normalizar con `oxidian/phone_utils.py`.
- Permisos: usar `oxidian/permissions.py` y los decoradores existentes.
- Fechas: mantener la convención UTC de modelos y servicios.
- CSS compartido va en `oxidian/static/css/`; evitar nuevos bloques grandes
  dentro de plantillas.
- JavaScript compartido va en `oxidian/static/js/`; evitar listeners duplicados
  y estado global nuevo.
- Secretos y datos runtime nunca se versionan.

## Comandos rápidos

```bash
cd oxidian
python3 -m compileall -q .
python3 -m unittest discover -s tests -q
cd ../chat
npm test
```

El despliegue no se deduce de la estructura local: seguir siempre
[`docs/OPERATIONS.md`](docs/OPERATIONS.md) y validar salud después de publicar.
