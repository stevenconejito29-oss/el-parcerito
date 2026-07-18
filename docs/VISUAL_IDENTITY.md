# Identidad visual colombiana

La tienda pública expresa nostalgia colombiana sin alterar los flujos de venta
ni depender de imágenes decorativas pesadas. La identidad se construye en tres
capas mantenibles:

- Los colores proceden de `COLOR_PRIMARIO`, `COLOR_ACENTO` y
  `COLOR_SECUNDARIO`. En CSS se consumen mediante los alias
  `--heritage-sun`, `--heritage-river` y `--heritage-clay`.
- Los textos emocionales viven en claves `UI_MENU_*`, `UI_CART_*` y
  `UI_FOOTER_*`, editables desde Configuración en Super Admin.
- Cordillera, tejido, emblemas e iconos son SVG o gradientes CSS. No añaden
  solicitudes de red, no bloquean interacción y desaparecen al imprimir.

## Criterios de uso

Las referencias culturales deben ser detalles de reconocimiento, no obstáculos:

- conservar contraste, áreas táctiles y jerarquía funcional;
- no sustituir estados operativos por color o símbolos decorativos;
- no introducir nombres de productos, precios o reglas comerciales en CSS;
- respetar `prefers-reduced-motion`, orientación horizontal y safe areas;
- validar móvil vertical/horizontal y tablet antes de publicar.

La navegación usa una casita, un grano de café dentro de la búsqueda y un
canasto para el carrito. El catálogo combina cordillera, cenefa tejida y una
firma tricolor discreta en tarjetas; el carrito reutiliza el mismo lenguaje.

Los símbolos se definen una sola vez en
`templates/partials/heritage_sprite.html`; `heritage.css` controla su forma y
composición. El programa técnico continúa almacenando puntos, pero la interfaz
permite darles un nombre propio mediante `UI_LOYALTY_NAME`,
`UI_LOYALTY_UNIT` y `UI_LOYALTY_UNIT_PLURAL` (por defecto, cafecitos).
