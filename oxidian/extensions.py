import os

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask import abort

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
except Exception:  # pragma: no cover - permite arrancar hasta instalar requirements
    Limiter = None

    def get_remote_address():
        return "global"

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
# Rate limiting. Activo solo si la libreria está disponible Y hay Redis
# configurado (memory:// no sirve con varios gunicorn workers — cada worker
# tendría su propio contador). En dev sin Redis el limiter se apaga
# explícitamente para no dar falso sentido de seguridad.
_LIMITER_STORAGE = os.environ.get("REDIS_URL", "memory://")
_LIMITER_ENABLED = bool(Limiter) and _LIMITER_STORAGE.startswith("redis://")
limiter = (
    Limiter(
        key_func=get_remote_address,
        # Sin default global — algunos assets estáticos y polling de sw.js/branding
        # generan >200 req/min por cliente legítimo. Aplicamos límites explícitos
        # solo a endpoints sensibles (login, MFA, check-address, bot).
        default_limits=[],
        storage_uri=_LIMITER_STORAGE,
        enabled=_LIMITER_ENABLED,
    )
    if Limiter else None
)
login_manager.login_view = "auth.login"
login_manager.login_message = "Inicia sesión para continuar."
login_manager.login_message_category = "warning"
login_manager.session_protection = "strong"


def get_or_404(model, ident):
    """SQLAlchemy 2.x friendly replacement for Model.query.get_or_404."""
    obj = db.session.get(model, ident)
    if obj is None:
        abort(404)
    return obj
