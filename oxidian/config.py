import os
import secrets
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


def _database_url():
    value = (os.environ.get("DATABASE_URL") or "").strip()
    if not value:
        raise RuntimeError(
            "DATABASE_URL es obligatoria. Usa docker-compose.cosmos-local.yml "
            "en local o configura PostgreSQL en Cosmos."
        )
    if value.startswith("sqlite"):
        raise RuntimeError(
            "SQLite ya no está soportado. Oxidian usa una única base PostgreSQL."
        )
    if not value.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("DATABASE_URL debe ser una URI PostgreSQL válida.")
    return value


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True

    PUNTOS_POR_EURO = int(os.environ.get("PUNTOS_POR_EURO", 1))
    PUNTOS_CANJE_RATIO = int(os.environ.get("PUNTOS_CANJE_RATIO", 100))
    ALERTA_CADUCIDAD_DIAS = int(os.environ.get("ALERTA_CADUCIDAD_DIAS", 7))
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024

    # Seguridad de sesión
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_NAME = "oxidian_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_PATH = "/"
    REMEMBER_COOKIE_DURATION = timedelta(days=7)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_PATH = "/"
    WTF_CSRF_TIME_LIMIT = 8 * 60 * 60


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}


class ProductionConfig(Config):
    DEBUG = False
    WTF_CSRF_ENABLED = True
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1").strip().lower() in (
        "1", "true", "yes", "si", "sí", "on"
    )
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    SESSION_COOKIE_NAME = "__Host-oxidian_session" if SESSION_COOKIE_SECURE else "oxidian_session"
    PREFERRED_URL_SCHEME = "https" if SESSION_COOKIE_SECURE else "http"

    # Pool de conexiones para PostgreSQL en producción.
    # Ajustable por entorno para escalar sin tocar código.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": int(os.environ.get("DB_POOL_SIZE", 5)),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", 10)),
        "pool_pre_ping": True,
        "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", 1800)),
        "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", 30)),
    }

    @classmethod
    def validate(cls):
        if not os.environ.get("SECRET_KEY"):
            raise ValueError("SECRET_KEY debe estar configurada en producción")
        if not os.environ.get("DATABASE_URL", "").startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("DATABASE_URL debe apuntar a PostgreSQL.")


config = {
    "development": DevelopmentConfig,
    "production":  ProductionConfig,
    "default":     DevelopmentConfig,
}
