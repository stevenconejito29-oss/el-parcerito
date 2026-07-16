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
