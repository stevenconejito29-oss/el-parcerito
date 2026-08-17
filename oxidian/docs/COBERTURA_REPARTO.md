# Cobertura de reparto por zonas

## Decisión de diseño

Carmona no se modela con el límite completo del municipio ni con un círculo.
El término municipal incluye territorio rural que no representa la distancia
real que puede recorrer un repartidor en patinete. La cobertura comercial se
guarda como configuración GeoJSON (`Polygon` o `MultiPolygon`) dibujada sobre
las calles que el negocio sí puede atender.

El sistema conserva los círculos anteriores para una transición segura, pero
un contorno configurado tiene prioridad. No hay coordenadas de Carmona dentro
del código: los límites se administran desde Super Admin → Zonas → Editar mapa.

## Flujo autoritativo

```text
dirección / ubicación del cliente
              │
              ▼
      geocodificación acotada
              │
              ▼
 motor único de cobertura ── fuera de todos los contornos ──► rechazar / recogida
              │
              ▼
 zona calculada por servidor
              │
       ┌──────┼────────┐
       ▼      ▼        ▼
 checkout    PWA    chatbot/API
       │      │        │
       └──────┴────────┘
              │
              ▼
 precio, tiempo, pedido y reparto con el mismo `zona_id`
```

El cliente nunca selecciona ni impone una zona. Aunque el chatbot envíe un
`zona_id`, el servidor utiliza el que corresponde a las coordenadas verificadas.
Esto evita precios equivocados y cruces entre canales.

## Reglas de solape y exclusión

- `Polygon` permite un casco urbano irregular y huecos para áreas inaccesibles.
- `MultiPolygon` permite incluir barriadas separadas sin cubrir el campo que hay
  entre ellas.
- Si dos polígonos contienen el mismo punto, gana el menor campo `orden` y luego
  el identificador, por lo que el resultado siempre es estable.
- Un polígono preciso prevalece frente a un círculo compatible en un solape.
- Si existen geometrías activas y ninguna contiene el punto, se rechaza el
  delivery; no se acepta por una lista o zona elegida manualmente.

## Operación recomendada para Carmona

1. Dibujar primero el casco que se atiende con normalidad, siguiendo calles y
   accesos, con un pequeño margen en los bordes residenciales.
2. Crear contornos separados para barriadas alcanzables que estén aisladas.
3. No unir dos núcleos con un polígono ancho: se usaría `MultiPolygon` o dos
   zonas con precios/tiempos diferentes.
4. Dejar fuera carreteras, polígonos industriales y urbanizaciones que no se
   puedan atender en el tiempo prometido.
5. Probar una dirección interior, una de borde y una exterior antes de activar
   ventas. La preapertura permite hacerlo sin exponer pedidos reales.

Los límites administrativos oficiales del CNIG son una referencia territorial,
no una cobertura de última milla. Por eso no se importan automáticamente como
zona comercial:
https://centrodedescargas.cnig.es/CentroDescargas/index.jsp

## Seguridad e integridad

La entrada se valida en servidor: tipos GeoJSON admitidos, rangos latitud/
longitud, cierre de anillos, área no nula, autocruces, máximo de vértices y
tamaño serializado. La migración es idempotente y los formularios parciales
conservan la geometría existente en lugar de borrarla.

Los círculos pueden retirarse gradualmente después de verificar el contorno.
No se eliminan automáticamente para no alterar tarifas o zonas históricas.

## Contrato con pedidos y canales

- Carrito y checkout solo ofrecen delivery cuando existe al menos una zona
  activa; si recogida sigue disponible, se conserva como alternativa.
- La tarifa que aparece antes de conocer la dirección es un rango informativo.
  El total solo incorpora el coste devuelto por la resolución autoritativa.
- Web y chatbot crean el pedido con el mismo motor de precios y cobertura. El
  `zona_id` recibido desde un cliente nunca sustituye la decisión del servidor.
- En web/PWA, una ubicación del dispositivo sólo se admite junto a una
  dirección completa y a la precisión informada por el navegador. Si la
  precisión es insuficiente o la calle geocodificada queda demasiado lejos del
  GPS, se rechaza y se solicita repetir la comprobación. WhatsApp conserva su
  contrato propio porque una ubicación compartida no siempre incluye precisión.
- Al confirmar se congelan en el pedido el coste cobrado, nombre de zona,
  tarifa base, tiempo estimado y tipo de cobertura. Tickets, historial y vistas
  de los roles leen ese snapshot, de modo que renombrar o archivar una zona no
  modifica pedidos anteriores.
- El `zona_id` se mantiene además como relación operativa para agrupación y
  reparto. El snapshot conserva la evidencia comercial; la relación permite
  trabajar el pedido actual.

## Reparto por franjas horarias (módulo opcional)

Toggle: `delivery_franjas_activo` en SiteConfig (default 0). Convive con
`delivery_inmediato_activo` (default 1); ambos pueden estar activos.

**Modelo**

- `DeliverySlot`: franja horaria concreta (fecha, hora_inicio, hora_fin) con
  `capacidad_max` (pedidos), `max_repartidores`, `activo`, y cierre
  configurable (`cierre_modo` ∈ `al_iniciar_siguiente` | `minutos_antes` |
  `hora_fija` + `cierre_valor`). Si el modo es NULL, hereda del default
  global.
- `SlotRepartidor`: asignación self-service del repartidor a la franja.
  Índice único parcial evita dos asignaciones activas del mismo rider a la
  misma franja.
- `Order.slot_id`: FK opcional. NULL para delivery inmediato o recogida.

**Flujo cliente**

1. En checkout, el cliente ve los próximos `delivery_franjas_horizonte_cliente_dias`
   días (default 7). La primera franja disponible cronológicamente aparece
   destacada como "sugerida"; el cliente confirma manualmente.
2. Al enviar el pedido, `reservar_franja` toma bloqueo (`SELECT ... FOR UPDATE`)
   sobre la franja y valida capacidad. Si está llena o cerrada, la UI muestra
   error explícito y destaca la siguiente sugerida disponible.
3. Cancelar el pedido libera el cupo automáticamente: el conteo activo se
   calcula en vivo excluyendo `estado='cancelado'`. No hay contador
   incremental que pueda desincronizarse.

**Flujo repartidor**

- Ve la lista de franjas de la próxima semana en `/repartidor/franjas`.
- Pulsa "tomar" en una franja libre; queda registrado como responsable hasta
  el `max_repartidores`. Puede liberarla si su plan cambia.
- Cuando llega al domicilio del cliente, pulsa "estoy en la puerta" en el
  pedido. Se dispara **un único WhatsApp** al cliente (política anti-baneo
  Meta): el resto de eventos van por push PWA. La notificación se apoya en
  la columna existente `Order.en_punto_encuentro` para trazabilidad.

**Flujo admin**

- CRUD de franjas en `/admin/delivery/franjas` con vista `delivery_franjas_horizonte_admin_dias`
  días (default 14).
- Clonar semana completa (respeta UNIQUE(fecha, hora_inicio, hora_fin) para no
  pisar planificación existente).
- Encender/apagar toggles del módulo desde `/superadmin/config`.

**Compatibilidad con el resto del sistema**

- El módulo se apaga sin efectos secundarios: todos los endpoints devuelven
  404 limpio cuando `delivery_franjas_activo=0`, y los pedidos existentes
  (con `slot_id=NULL`) siguen operando en el flujo inmediato.
- No modifica el snapshot congelado (`OrderItem.metadata_json`): el `slot_id`
  es una relación operativa, no un dato comercial que necesite congelarse.
- Zonas y cobertura geo siguen aplicando dentro de la franja seleccionada
  (una franja no cambia si el cliente está en cobertura, solo cuándo llega).
