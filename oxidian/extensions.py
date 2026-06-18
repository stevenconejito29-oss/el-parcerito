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
limiter = (
    Limiter(
        key_func=get_remote_address,
        default_limits=["200 per minute"],
        storage_uri=os.environ.get("REDIS_URL", "memory://"),
        enabled=False,
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
