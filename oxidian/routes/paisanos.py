"""Blueprint /paisanos + /herramientas — recursos para la comunidad colombiana."""
from __future__ import annotations

from flask import Blueprint, render_template

from paisanos_service import snapshot_para_vista

paisanos_bp = Blueprint("paisanos", __name__)


@paisanos_bp.route("/paisanos", endpoint="portal")
def portal_paisanos():
    """Portal en vivo: cotización COP, clima, noticias, extranjería."""
    data = snapshot_para_vista()
    return render_template("public/paisanos.html", data=data)


@paisanos_bp.route("/herramientas", endpoint="herramientas")
def herramientas_pdf():
    """Landing de herramientas PDF (proxy visual a Stirling-PDF)."""
    stirling_base = "https://pdf.elparcerito.com"
    tarjetas = [
        {"titulo": "PDF a Word", "descripcion": "Convierte tus PDFs en documentos editables.",
         "icon": "📝", "ruta": "/convert/pdf-to-word"},
        {"titulo": "Word a PDF", "descripcion": "Manda tu currículum o formulario como PDF.",
         "icon": "📄", "ruta": "/convert/file-to-pdf"},
        {"titulo": "Unir PDFs", "descripcion": "Combina varios documentos en un solo archivo.",
         "icon": "📎", "ruta": "/merge-pdfs"},
        {"titulo": "Comprimir PDF", "descripcion": "Reduce el tamaño para adjuntar por email.",
         "icon": "🗜️", "ruta": "/compress-pdf"},
        {"titulo": "PDF a imagen (JPG)", "descripcion": "Extrae cada página como imagen.",
         "icon": "🖼️", "ruta": "/pdf-to-img"},
        {"titulo": "OCR — extraer texto", "descripcion": "Convierte escaneos en texto seleccionable.",
         "icon": "🔍", "ruta": "/ocr-pdf"},
        {"titulo": "Firmar PDF", "descripcion": "Añade tu firma para trámites y contratos.",
         "icon": "✍️", "ruta": "/sign"},
        {"titulo": "Dividir y rotar", "descripcion": "Corrige la orientación o separa páginas.",
         "icon": "✂️", "ruta": "/multi-tool"},
    ]
    return render_template(
        "public/herramientas.html",
        stirling_base=stirling_base, tarjetas=tarjetas,
    )
