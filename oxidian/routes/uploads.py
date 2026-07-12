"""
Rutas HTTP para subir y servir imágenes desde el panel admin.

La lógica de guardado, redimensionado y borrado seguro vive en
`image_service.py` para ser reutilizable desde otras rutas (admin, etc)
sin cruzar imports entre blueprints.
"""
import uuid
import logging
import hashlib
import time as _t
from pathlib import Path
from functools import wraps

from flask import Blueprint, send_from_directory, request, jsonify, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from extensions import db, get_or_404
from models import AdminFeature, Product, SiteConfig, Categoria
from image_service import (
    ALLOWED_EXT,
    IMAGES_DIR,
    MAX_SIZES,
    PILLOW_OK,
    delete_image,
    save_image,
)

try:
    from PIL import Image
except ImportError:  # image_service ya expone PILLOW_OK; Image local solo si Pillow OK
    Image = None

uploads_bp = Blueprint("uploads", __name__)
logger = logging.getLogger(__name__)



def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def _valid_image_file(file_obj) -> bool:
    """Verifica contenido real de imagen y restaura el cursor del stream."""
    ext = file_obj.filename.rsplit(".", 1)[-1].lower() if "." in (file_obj.filename or "") else ""

    pos = file_obj.stream.tell()
    try:
        if PILLOW_OK:
            with Image.open(file_obj.stream) as img:
                img.verify()
            return True

        header = file_obj.stream.read(16)
        return (
            header.startswith(b"\xff\xd8\xff")
            or header.startswith(b"\x89PNG\r\n\x1a\n")
            or header.startswith((b"GIF87a", b"GIF89a"))
            or (len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP")
        )
    except Exception:
        return False
    finally:
        file_obj.stream.seek(pos)


def _validate_upload_file(file_obj) -> str | None:
    """Devuelve mensaje de error o None si el archivo es válido.
    Mensajes explícitos para el usuario final (no técnicos)."""
    if not file_obj or not file_obj.filename:
        return "No se recibió ningún archivo."
    if not _allowed(file_obj.filename):
        ext = file_obj.filename.rsplit(".", 1)[-1].lower() if "." in file_obj.filename else "?"
        return f"Formato .{ext} no admitido. Usa JPG, PNG, WEBP o GIF."
    if not _valid_image_file(file_obj):
        return "El archivo parece dañado o no es una imagen real."
    # Verificar tamaño máximo (10 MB por env, ya limitado por MAX_CONTENT_LENGTH)
    try:
        file_obj.stream.seek(0, 2)  # end
        size = file_obj.stream.tell()
        file_obj.stream.seek(0)
        if size > 10 * 1024 * 1024:
            return f"Imagen demasiado pesada ({size // (1024*1024)} MB). Máximo 10 MB."
        if size < 100:
            return "El archivo está vacío o corrupto."
    except Exception:
        pass
    return None


def _cleanup_old_brand_files(subcarpeta: str, keep_prefix: str, max_keep: int = 3):
    """Mantiene solo los N archivos más recientes que empiezan por keep_prefix.
    Evita que el volumen crezca sin control tras muchas subidas de logo."""
    try:
        folder = IMAGES_DIR / subcarpeta
        if not folder.exists():
            return
        candidates = sorted(
            [f for f in folder.iterdir() if f.is_file() and f.name.startswith(keep_prefix)],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in candidates[max_keep:]:
            try:
                old.unlink()
                logger.info("cleanup: eliminado %s", old.name)
            except OSError:
                pass
    except Exception as exc:
        logger.warning("cleanup fallo en %s/%s: %s", subcarpeta, keep_prefix, exc)


def _save_brand_asset(file_obj, subcarpeta: str, kind: str) -> dict:
    """Guarda un asset de marca (logo/icon/hero) con nombre único basado en
    hash+timestamp para invalidar caches de forma garantizada. Limpia versiones
    antiguas y devuelve dict con url versionada, ruta y bytes."""
    # Nombre único = kind_<hash8>_<epoch>.<ext> → invalidar cache siempre
    file_obj.stream.seek(0)
    contenido = file_obj.stream.read(65536)
    file_obj.stream.seek(0)
    content_hash = hashlib.sha256(contenido).hexdigest()[:8]
    ts = int(_t.time())
    stem = f"{kind}_{content_hash}_{ts}"
    ruta_relativa = save_image(file_obj, subcarpeta, stem)
    _cleanup_old_brand_files(subcarpeta, keep_prefix=f"{kind}_", max_keep=3)
    # URL con cache-buster explícito por si algún proxy ignora el nombre
    url = f"/uploads/{ruta_relativa}?v={ts}"
    return {"ruta": ruta_relativa, "url": url}


def admin_required_upload(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.rol not in ("admin", "super_admin"):
            return jsonify({"ok": False, "error": "Sin permiso"}), 403
        return f(*args, **kwargs)
    return decorated


# ── Servir imágenes ───────────────────────────────────────────────────────────

@uploads_bp.route("/uploads/<subcarpeta>/<filename>")
def serve_image(subcarpeta, filename):
    """Sirve cualquier imagen del directorio de imágenes compartido."""
    if subcarpeta not in MAX_SIZES and subcarpeta not in ("misc", "icon"):
        abort(404)
    # secure_filename previene path traversal en el nombre de archivo
    safe = secure_filename(filename)
    if not safe:
        abort(404)
    folder = IMAGES_DIR / subcarpeta
    return send_from_directory(str(folder), safe)


# ── Subir imagen de producto ──────────────────────────────────────────────────

@uploads_bp.route("/uploads/producto/<int:producto_id>", methods=["POST"])
@admin_required_upload
def subir_imagen_producto(producto_id):
    if (
        current_user.rol == "admin"
        and not AdminFeature.tiene_acceso(current_user.id, "productos")
    ):
        return jsonify({"ok": False, "error": "Sin acceso al módulo productos"}), 403
    producto = get_or_404(Product, producto_id)

    if "imagen" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo"}), 400

    f = request.files["imagen"]
    error = _validate_upload_file(f)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    # Borrar imagen anterior si existe
    if producto.imagen_url and producto.imagen_url.startswith("productos/"):
        delete_image(producto.imagen_url)

    ruta = save_image(f, "productos")
    producto.imagen_url = ruta
    db.session.commit()

    return jsonify({"ok": True, "ruta": ruta, "url": f"/uploads/{ruta}"})


# ── Subir logo del negocio ────────────────────────────────────────────────────

@uploads_bp.route("/uploads/logo", methods=["POST"])
@admin_required_upload
def subir_logo():
    if "imagen" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo."}), 400
    f = request.files["imagen"]
    error = _validate_upload_file(f)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    try:
        result = _save_brand_asset(f, "logo", "logo")
    except Exception as exc:
        logger.error("subir_logo fallo: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": "No se pudo procesar la imagen. Prueba con otro archivo."}), 500
    SiteConfig.set("LOGO_URL", result["url"], descripcion="Logo del negocio")
    db.session.commit()
    return jsonify({"ok": True, **result})


# ── Subir imagen de categoría ─────────────────────────────────────────────────

@uploads_bp.route("/uploads/categoria/<int:cat_id>", methods=["POST"])
@admin_required_upload
def subir_imagen_categoria(cat_id):
    cat = get_or_404(Categoria, cat_id)

    if "imagen" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo"}), 400

    f = request.files["imagen"]
    error = _validate_upload_file(f)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    if cat.imagen_url and cat.imagen_url.startswith("categorias/"):
        delete_image(cat.imagen_url)

    ruta = save_image(f, "categorias", f"cat_{cat_id}.jpg")
    cat.imagen_url = ruta
    db.session.commit()

    return jsonify({"ok": True, "ruta": ruta, "url": f"/uploads/{ruta}"})


# ── Subir banner ──────────────────────────────────────────────────────────────

@uploads_bp.route("/uploads/banner", methods=["POST"])
@admin_required_upload
def subir_banner():
    if "imagen" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo"}), 400

    f = request.files["imagen"]
    error = _validate_upload_file(f)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    ruta = save_image(f, "banners")
    return jsonify({"ok": True, "ruta": ruta, "url": f"/uploads/{ruta}"})


# ── Subir icono / favicon de la app ──────────────────────────────────────────

@uploads_bp.route("/uploads/icon", methods=["POST"])
@admin_required_upload
def subir_icon():
    if "imagen" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo."}), 400
    f = request.files["imagen"]
    error = _validate_upload_file(f)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    try:
        result = _save_brand_asset(f, "icon", "icon")
    except Exception as exc:
        logger.error("subir_icon fallo: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": "No se pudo procesar el icono. Prueba con otro archivo."}), 500
    SiteConfig.set("APP_ICON_URL", result["url"], descripcion="Icono / favicon de la app")
    db.session.commit()
    return jsonify({"ok": True, **result})


@uploads_bp.route("/uploads/hero", methods=["POST"])
@admin_required_upload
def subir_hero():
    if "imagen" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo"}), 400

    f = request.files["imagen"]
    error = _validate_upload_file(f)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    try:
        result = _save_brand_asset(f, "hero", "hero")
    except Exception as exc:
        logger.error("subir_hero fallo: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": "No se pudo procesar la imagen. Prueba con otro archivo."}), 500
    SiteConfig.set("HERO_IMAGE_URL", result["url"], descripcion="Imagen de cabecera de la tienda")
    db.session.commit()
    return jsonify({"ok": True, **result})


# ── Listar imágenes disponibles ───────────────────────────────────────────────

@uploads_bp.route("/uploads/listar/<subcarpeta>")
@admin_required_upload
def listar_imagenes(subcarpeta):
    if subcarpeta not in {*MAX_SIZES, "misc"}:
        return jsonify({"ok": False, "error": "Subcarpeta inválida"}), 400
    folder = IMAGES_DIR / subcarpeta
    if not folder.exists():
        return jsonify({"ok": True, "archivos": []})
    archivos = [
        {"nombre": f.name, "url": f"/uploads/{subcarpeta}/{f.name}", "ruta": f"{subcarpeta}/{f.name}"}
        for f in folder.iterdir()
        if f.is_file() and f.suffix.lower().lstrip(".") in ALLOWED_EXT
    ]
    return jsonify({"ok": True, "archivos": archivos})


# ── Eliminar imagen ───────────────────────────────────────────────────────────

@uploads_bp.route("/uploads/eliminar", methods=["POST"])
@admin_required_upload
def eliminar_imagen():
    ruta = (request.json.get("ruta", "") if request.is_json else "").strip()
    if not ruta:
        return jsonify({"ok": False, "error": "Ruta requerida"}), 400
    # Prevenir path traversal: la ruta debe ser 'subcarpeta/archivo' sin '..'
    partes = Path(ruta).parts
    if len(partes) != 2 or ".." in partes or partes[0] not in {*MAX_SIZES, "misc"}:
        return jsonify({"ok": False, "error": "Ruta no permitida"}), 400

    ok = delete_image(ruta)
    if ok:
        _limpiar_referencias_bd(ruta)

    return jsonify({"ok": ok})


def _limpiar_referencias_bd(ruta: str):
    """Elimina referencias a la imagen borrada en Product, Categoria y SiteConfig."""
    try:
        Product.query.filter_by(imagen_url=ruta).update({"imagen_url": None})
        Categoria.query.filter_by(imagen_url=ruta).update({"imagen_url": None})
        logo_actual = SiteConfig.get("LOGO_URL", "")
        if logo_actual == f"/uploads/{ruta}":
            SiteConfig.set("LOGO_URL", "", descripcion="Logo del negocio")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning("Error limpiando referencias BD para %s: %s", ruta, e, exc_info=True)


# La lógica de save_image y delete_image vive ahora en image_service.py.
# Se re-exponen arriba como aliases privados para preservar callers históricos.
