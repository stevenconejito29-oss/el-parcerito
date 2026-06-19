from flask import Blueprint, jsonify
from flask_login import login_required, current_user
from services import estado_cola

presencia_bp = Blueprint("presencia", __name__)


@presencia_bp.route("/config-publica", methods=["GET"])
def config_publica():
    """Configuración pública (sin auth) para widgets del frontend."""
    from store_config import get_store_profile
    profile = get_store_profile()
    return jsonify({
        "telefono": profile["telefono"],
        "nombre": profile["nombre"],
        "bizum_telefono": profile["bizum_telefono"],
        "bizum_habilitado": profile["bizum_habilitado"],
        "efectivo_habilitado": profile["efectivo_habilitado"],
        "ciudad": profile["ciudad"],
    })


@presencia_bp.route("/ping", methods=["POST"])
@login_required
def ping():
    """Keep-alive de presencia. El before_request ya actualizó last_seen si hacía falta."""
    return jsonify({"ok": True, "rol": current_user.rol})


@presencia_bp.route("/cola", methods=["GET"])
@login_required
def cola_json():
    """Estado de la cola en JSON para el dashboard."""
    if current_user.rol not in ("admin", "super_admin"):
        return jsonify({"error": "Sin permiso"}), 403
    return jsonify(estado_cola())
