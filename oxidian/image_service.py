"""Almacenamiento y procesamiento de imágenes subidas por el panel admin.

Módulo de infraestructura: recibe streams de Werkzeug (`FileStorage`),
redimensiona con Pillow si está disponible, elige formato de salida en
función del canal alfa, escribe en disco y devuelve la ruta relativa
para persistir en la BD.

Cualquier ruta o servicio que necesite guardar o borrar una imagen debe
llamar a las dos funciones públicas — `save_image` y `delete_image` — en
vez de manipular `IMAGES_DIR` directamente. Esto centraliza el sandbox
(evitar path traversal), la política de conversión y el logging.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

try:
    from PIL import Image
    PILLOW_OK = True
except ImportError:  # pragma: no cover — entorno mínimo sin dep opcional
    PILLOW_OK = False

logger = logging.getLogger(__name__)

# Carpeta raíz de imágenes (siempre dentro del paquete oxidian/).
IMAGES_DIR = Path(__file__).parent / "images"

# Extensiones aceptadas de subida. Todo lo demás se rechaza.
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp", "gif"}

# Tamaño máximo (ancho, alto) por subcarpeta. Pillow redimensiona
# preservando aspect ratio con `thumbnail`. Subcarpetas no listadas caen
# al default (1000, 1000).
MAX_SIZES = {
    "productos":   (800, 800),
    "logo":        (400, 400),
    "categorias":  (600, 400),
    "banners":     (1200, 400),
    "icon":        (512, 512),
    "hero":        (1600, 900),
    "test-images": (1200, 800),
    "showcase":    (1200, 860),
}


def save_image(file_obj, subcarpeta: str, nombre_custom: str | None = None) -> str:
    """Guarda un archivo subido y devuelve su ruta relativa (`subcarpeta/xyz.jpg`).

    Reglas:
    - Si ``nombre_custom`` trae extensión, se respeta el stem sin ella.
    - Si no, se genera un uuid corto.
    - PNG con canal alfa se preservan como PNG.
    - Todo lo demás se reencoda a JPEG (menor tamaño, mayor compatibilidad).
    - Si Pillow no está instalado, se guarda el archivo tal cual sin resizing.
    """
    ext_original = (
        file_obj.filename.rsplit(".", 1)[1].lower()
        if file_obj.filename and "." in file_obj.filename
        else "jpg"
    )
    if ext_original not in ALLOWED_EXT:
        ext_original = "jpg"

    if nombre_custom:
        stem = nombre_custom.rsplit(".", 1)[0] if "." in nombre_custom else nombre_custom
    else:
        stem = uuid.uuid4().hex[:12]

    (IMAGES_DIR / subcarpeta).mkdir(parents=True, exist_ok=True)

    tmp_nombre = f"{stem}.{ext_original}"
    tmp_destino = IMAGES_DIR / subcarpeta / tmp_nombre
    file_obj.save(str(tmp_destino))

    if not PILLOW_OK:
        return f"{subcarpeta}/{tmp_nombre}"

    max_w, max_h = MAX_SIZES.get(subcarpeta, (1000, 1000))
    try:
        with Image.open(str(tmp_destino)) as img:
            tiene_alpha = img.mode in ("RGBA", "LA", "P")
            img.thumbnail((max_w, max_h), Image.LANCZOS)

            if ext_original == "png" and tiene_alpha:
                if img.mode == "P":
                    img = img.convert("RGBA")
                nombre_final = f"{stem}.png"
                destino_final = IMAGES_DIR / subcarpeta / nombre_final
                img.save(str(destino_final), "PNG", optimize=True)
            else:
                img = img.convert("RGB")
                nombre_final = f"{stem}.jpg"
                destino_final = IMAGES_DIR / subcarpeta / nombre_final
                img.save(str(destino_final), "JPEG", quality=85, optimize=True)

        if tmp_destino != destino_final and tmp_destino.exists():
            tmp_destino.unlink(missing_ok=True)

        return f"{subcarpeta}/{nombre_final}"

    except Exception as exc:
        logger.warning("Pillow error al procesar imagen: %s", exc, exc_info=True)
        return f"{subcarpeta}/{tmp_nombre}"


def delete_image(ruta: str) -> bool:
    """Borra un archivo dentro de IMAGES_DIR de forma segura.

    Rechaza:
    - Rutas vacías.
    - URLs absolutas (http/https) — nada que borrar en disco local.
    - Rutas que resuelvan fuera de IMAGES_DIR (path traversal).

    Devuelve True solo si el archivo existía y fue eliminado.
    """
    try:
        raw = (ruta or "").strip()
        if not raw or raw.startswith(("http://", "https://")):
            return False
        base = IMAGES_DIR.resolve()
        candidato = (base / raw.lstrip("/")).resolve()
        if base not in candidato.parents:
            logger.warning("Ruta de imagen fuera del directorio permitido: %s", ruta)
            return False
        if candidato.exists() and candidato.is_file():
            candidato.unlink()
            return True
    except Exception:
        logger.warning("No se pudo borrar imagen %s", ruta, exc_info=True)
    return False
