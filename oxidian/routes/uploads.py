"""
Gestión de imágenes: subida, redimensionado y servido.
Las imágenes se guardan en <proyecto>/images/ y se sirven en /uploads/<subcarpeta>/<archivo>
"""
import uuid
import logging
from pathlib import Path
from functools import wraps

from flask import Blueprint, send_from_directory, request, jsonify, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from extensions import db, get_or_404
from models import AdminFeature, Product, SiteConfig, Categoria

try:
    from PIL import Image
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False

uploads_bp = Blueprint("uploads", __name__)
logger = logging.getLogger(__name__)

# ── Ruta base de imágenes — dentro de oxidian/ para estructura limpia de 3 carpetas ──
IMAGES_DIR = Path(__file__).parent.parent / "images"
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp", "gif"}

# Tamaños máximos por subcarpeta (ancho x alto en px)
MAX_SIZES = {
    "productos":  (800, 800),
    "logo":       (400, 400),
    "categorias": (600, 400),
    "banners":    (1200, 400),
    "icon":       (512, 512),
    "hero":       (1600, 900),
    "test-images": (1200, 800),
    "showcase": (1200, 860),
}


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
    if not file_obj.filename or not _allowed(file_obj.filename):
        return "Tipo de archivo no permitido"
    if not _valid_image_file(file_obj):
        return "El archivo no es una imagen válida"
    return None


def _save_image(file_obj, subcarpeta: str, nombre_custom: str = None) -> str:
    """
    Guarda el archivo, lo redimensiona si Pillow está disponible,
    y devuelve la ruta relativa tipo 'productos/abc123.jpg'.

    Reglas:
    - Si `nombre_custom` incluye extensión (.jpg, .png, etc.) se respeta.
    - Si no, se deriva la extensión del archivo subido.
    - Los PNG con canal alfa se guardan como PNG preservando transparencia.
    - Todos los demás se convierten a JPEG (menor tamaño, mayor compatibilidad).
    - El directorio de destino se crea si no existe.
    """
    # Derivar extensión real del archivo subido
    ext_original = (
        file_obj.filename.rsplit(".", 1)[1].lower()
        if "." in file_obj.filename else "jpg"
    )
    if ext_original not in ALLOWED_EXT:
        ext_original = "jpg"

    # Resolver nombre final: si nombre_custom ya tiene extensión, usarla tal cual
    if nombre_custom:
        stem = nombre_custom.rsplit(".", 1)[0] if "." in nombre_custom else nombre_custom
    else:
        stem = uuid.uuid4().hex[:12]

    # Crear directorio si no existe
    (IMAGES_DIR / subcarpeta).mkdir(parents=True, exist_ok=True)

    # Guardar archivo temporal con extensión original para que Pillow pueda leerlo
    tmp_nombre = f"{stem}.{ext_original}"
    tmp_destino = IMAGES_DIR / subcarpeta / tmp_nombre
    file_obj.save(str(tmp_destino))

    if not PILLOW_OK:
        # Casos sin Pillow: usar el archivo tal cual tras validación de cabecera.
        return f"{subcarpeta}/{tmp_nombre}"

    max_w, max_h = MAX_SIZES.get(subcarpeta, (1000, 1000))
    try:
        with Image.open(str(tmp_destino)) as img:
            # Leer el modo ANTES de hacer ninguna conversión
            tiene_alpha = img.mode in ("RGBA", "LA", "P")
            img.thumbnail((max_w, max_h), Image.LANCZOS)

            if ext_original == "png" and tiene_alpha:
                # Preservar transparencia para logos y banners PNG
                if img.mode == "P":
                    img = img.convert("RGBA")
                nombre_final = f"{stem}.png"
                destino_final = IMAGES_DIR / subcarpeta / nombre_final
                img.save(str(destino_final), "PNG", optimize=True)
            else:
                # Convertir todo lo demás a JPEG
                img = img.convert("RGB")
                nombre_final = f"{stem}.jpg"
                destino_final = IMAGES_DIR / subcarpeta / nombre_final
                img.save(str(destino_final), "JPEG", quality=85, optimize=True)

        # Limpiar el archivo temporal si es diferente al final
        if tmp_destino != destino_final and tmp_destino.exists():
            tmp_destino.unlink(missing_ok=True)

        return f"{subcarpeta}/{nombre_final}"

    except Exception as e:
        logger.warning("Pillow error al procesar imagen: %s", e, exc_info=True)
        # Si Pillow falló, devolver el archivo original sin procesar
        return f"{subcarpeta}/{tmp_nombre}"


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
        _borrar_imagen(producto.imagen_url)

    ruta = _save_image(f, "productos")
    producto.imagen_url = ruta
    db.session.commit()

    return jsonify({"ok": True, "ruta": ruta, "url": f"/uploads/{ruta}"})


# ── Subir logo del negocio ────────────────────────────────────────────────────

@uploads_bp.route("/uploads/logo", methods=["POST"])
@admin_required_upload
def subir_logo():
    if "imagen" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo"}), 400

    f = request.files["imagen"]
    error = _validate_upload_file(f)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    # Siempre nombrar 'logo.jpg' para sobreescribir
    ruta = _save_image(f, "logo", "logo.jpg")
    SiteConfig.set("LOGO_URL", f"/uploads/{ruta}", descripcion="Logo del negocio")
    db.session.commit()

    return jsonify({"ok": True, "ruta": ruta, "url": f"/uploads/{ruta}"})


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
        _borrar_imagen(cat.imagen_url)

    ruta = _save_image(f, "categorias", f"cat_{cat_id}.jpg")
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

    ruta = _save_image(f, "banners")
    return jsonify({"ok": True, "ruta": ruta, "url": f"/uploads/{ruta}"})


# ── Subir icono / favicon de la app ──────────────────────────────────────────

@uploads_bp.route("/uploads/icon", methods=["POST"])
@admin_required_upload
def subir_icon():
    if "imagen" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo"}), 400

    f = request.files["imagen"]
    error = _validate_upload_file(f)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    ruta = _save_image(f, "icon", "app-icon.png")
    SiteConfig.set("APP_ICON_URL", f"/uploads/{ruta}", descripcion="Icono / favicon de la app")
    db.session.commit()

    return jsonify({"ok": True, "ruta": ruta, "url": f"/uploads/{ruta}"})


@uploads_bp.route("/uploads/hero", methods=["POST"])
@admin_required_upload
def subir_hero():
    if "imagen" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo"}), 400

    f = request.files["imagen"]
    error = _validate_upload_file(f)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    ruta = _save_image(f, "hero", "hero.jpg")
    SiteConfig.set("HERO_IMAGE_URL", f"/uploads/{ruta}", descripcion="Imagen de cabecera de la tienda")
    db.session.commit()

    return jsonify({"ok": True, "ruta": ruta, "url": f"/uploads/{ruta}"})


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

    ok = _borrar_imagen(ruta)
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


def _borrar_imagen(ruta: str) -> bool:
    try:
        raw = (ruta or "").strip()
        if not raw or raw.startswith(("http://", "https://")):
            return False
        base = IMAGES_DIR.resolve()
        p = (base / raw.lstrip("/")).resolve()
        if base not in p.parents:
            logger.warning("Ruta de imagen fuera del directorio permitido: %s", ruta)
            return False
        if p.exists() and p.is_file():
            p.unlink()
            return True
    except Exception:
        logger.warning("No se pudo borrar imagen %s", ruta, exc_info=True)
    return False
