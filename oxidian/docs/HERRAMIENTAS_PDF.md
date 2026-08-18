# Landing /herramientas — Herramientas PDF

Landing pública que dirige a Stirling-PDF, con SEO orientado a búsquedas colombianas/extranjeras en España ("convertir pdf gratis online", "nie", "residencia").

## Mapa de tarjetas

Todas las tarjetas abren `target="_blank"` a `https://pdf.elparcerito.com<ruta>`.

| Título en tarjeta | Ruta Stirling |
|---|---|
| PDF a Word | `/convert/pdf-to-word` |
| Word a PDF | `/convert/file-to-pdf` |
| Unir PDFs | `/merge-pdfs` |
| Comprimir PDF | `/compress-pdf` |
| PDF a imagen (JPG) | `/pdf-to-img` |
| OCR — extraer texto | `/ocr-pdf` |
| Firmar PDF | `/sign` |
| Dividir y rotar | `/multi-tool` |

## Dominio público vs LAN

- **Público (Cloudflare Tunnel):** `https://pdf.elparcerito.com/` → HTTPS público, es el que enlaza la landing.
- **LAN interna:** `http://192.168.1.32:<puerto>` — no se enlaza desde la web.

## Política de datos

Stirling-PDF corre en nuestro servidor (`192.168.1.32`). Los archivos se procesan localmente y se borran al terminar el trabajo. **No se guardan copias**. Este mensaje aparece en el pie de la landing.

## Archivos

- Ruta: `routes/paisanos.py` (compartido con `/paisanos`, misma familia de recursos comunitarios).
- Template: `templates/public/herramientas.html`.
- Botón "Herramientas 🛠️" en `ox-bottom-nav` y `ep-footer-nav` de `templates/base.html`.
