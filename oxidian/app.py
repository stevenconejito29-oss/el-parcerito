import json
import hashlib
import logging
import os
import secrets
import time
import hmac
from datetime import date, datetime, timedelta, timezone

from flask import Flask, render_template, request, send_from_directory, g
from flask_wtf.csrf import generate_csrf
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from config import config
from extensions import db, login_manager, csrf, limiter
from flask_login import current_user


def _to_bool(val, default=False):
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "si", "sí", "on", "yes")


def _env_default(key, fallback=""):
    return os.environ.get(key, fallback)


def _seed_password():
    return os.environ.get("SEED_PASSWORD") or secrets.token_urlsafe(24)


def _asset_version(app):
    """Huella del frontend para invalidar caches PWA en cada cambio real."""
    digest = hashlib.sha256()
    for relative_path in (
        "css/tailwind.generated.css",
        "css/oxidian.css",
        "js/carrito.js",
        "sw.js",
    ):
        path = os.path.join(app.static_folder, relative_path)
        try:
            with open(path, "rb") as asset:
                digest.update(asset.read())
        except OSError:
            digest.update(relative_path.encode("utf-8"))
    return digest.hexdigest()[:12]


def _seed_vapid_keys(app):
    """Siembra las claves VAPID en SiteConfig si no existen."""
    with app.app_context():
        try:
            import base64
            from cryptography.hazmat.primitives import serialization
            from py_vapid import Vapid
            from models import SiteConfig
            if not SiteConfig.get("VAPID_PUBLIC_KEY"):
                vapid_public = os.environ.get("VAPID_PUBLIC_KEY")
                vapid_private = os.environ.get("VAPID_PRIVATE_KEY")
                if not vapid_public or not vapid_private:
                    vapid = Vapid()
                    vapid.generate_keys()
                    public_raw = vapid.public_key.public_bytes(
                        encoding=serialization.Encoding.X962,
                        format=serialization.PublicFormat.UncompressedPoint,
                    )
                    vapid_public = base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode("ascii")
                    vapid_private = vapid.private_pem().decode("utf-8")
                SiteConfig.set("VAPID_PUBLIC_KEY",  vapid_public,
                               descripcion="Clave pública VAPID para Web Push")
                SiteConfig.set("VAPID_PRIVATE_KEY", vapid_private,
                               descripcion="Clave privada VAPID para Web Push (NO compartir)")
                db.session.commit()
        except Exception:
            app.logger.exception("No se pudieron sembrar claves VAPID")


def create_app(env="default"):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    app = Flask(__name__)
    app.config.from_object(config[env])
    app.config["ASSET_VERSION"] = _asset_version(app)
    if _to_bool(os.environ.get("TRUST_PROXY_HEADERS"), env == "production"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    if hasattr(config[env], "validate"):
        config[env].validate()
    _validar_config_runtime(app, env)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    if limiter is not None:
        limiter.enabled = not app.config.get("TESTING", False)
        limiter.init_app(app)

    from models import User, utcnow

    @login_manager.user_loader
    def load_user(user_id):
        try:
            ident = int(user_id)
        except (TypeError, ValueError):
            return None
        user = db.session.get(User, ident)
        return user if user and user.puede_iniciar_sesion else None

    @app.before_request
    def log_request_start():
        g.start_time = time.time()

    # ── MFA / session version ────────────────────────────────────
    # 1. Si la cookie viene marcada con una versión MFA anterior a la actual del
    #    usuario, se invalida la sesión (cierre de sesión global cuando cambian
    #    contraseña o reset MFA).
    # 2. Si el usuario tiene un rol con MFA obligatorio (super_admin) pero aún
    #    no lo ha activado, se le redirige a /auth/perfil/mfa.
    @app.before_request
    def enforce_mfa():
        from flask import session as _sess, redirect as _redirect, url_for as _url_for, request as _req
        from flask_login import logout_user as _logout
        from routes.auth import ROLES_MFA_OBLIGATORIO

        if not current_user.is_authenticated:
            return None
        cookie_version = int(_sess.get("mfa_v", 0) or 0)
        user_version = int(current_user.mfa_session_version or 0)
        if cookie_version < user_version:
            _logout()
            _sess.clear()
            return _redirect(_url_for("auth.login"))

        path = _req.path or ""
        permitido_sin_mfa = (
            path.startswith("/auth/")
            or path.startswith("/static/")
            or path.startswith("/uploads/")
            or path.startswith("/health")
            or path.startswith("/api/push")
            or path == "/sw.js"
            or path == "/manifest.webmanifest"
            or path == "/favicon.ico"
        )
        if permitido_sin_mfa:
            return None
        # En dev/local podemos desactivar el setup obligatorio con
        # OXIDIAN_MFA_ENFORCED=0 sin perder el resto del flujo MFA
        # (los usuarios que ya tengan MFA activo seguirán pasando por challenge).
        enforced = os.environ.get("OXIDIAN_MFA_ENFORCED", "1").strip().lower()
        if enforced not in ("0", "false", "no"):
            if current_user.rol in ROLES_MFA_OBLIGATORIO and not current_user.mfa_enabled:
                return _redirect(_url_for("auth.mfa_setup"))
        return None

    # Actualizar presencia en cada request autenticado.
    # Se throttlea a 1 write/minuto por usuario para no saturar la BD bajo carga.
    @app.before_request
    def actualizar_presencia():
        if current_user.is_authenticated:
            from flask import session as _sess
            _sess.permanent = True
            last = current_user.last_seen
            now  = datetime.now(timezone.utc).replace(tzinfo=None)
            if last is None or (now - last).total_seconds() >= 60:
                current_user.marcar_activo()
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    app.logger.exception("No se pudo actualizar presencia de usuario %s", current_user.id)

    @app.route("/sw.js")
    def service_worker():
        response = send_from_directory(app.static_folder, "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.route("/manifest.webmanifest")
    def web_manifest():
        from models import SiteConfig

        nombre = SiteConfig.get("NOMBRE_NEGOCIO", _env_default("NOMBRE_NEGOCIO", "Oxidian"))
        short_name = (nombre[:12] or "Oxidian").strip()
        manifest = {
            "name": nombre,
            "short_name": short_name,
            "description": "Menu online, carrito, puntos y seguimiento de pedidos.",
            "start_url": "/?source=pwa",
            "scope": "/",
            "display": "standalone",
            "display_override": ["standalone", "minimal-ui", "browser"],
            "background_color": "#FFFDF8",
            "theme_color": SiteConfig.get("COLOR_PRIMARIO", "#D9961A"),
            "orientation": "portrait-primary",
            "lang": "es",
            "dir": "ltr",
            "categories": ["food", "shopping"],
            "id": "oxidian-menu",
            "icons": [],
            "shortcuts": [
                {"name": "Ver menu",   "short_name": "Menu",    "description": "Explora el catalogo",    "url": "/",        "icons": [{"src": "/static/pwa-icon-192.png", "sizes": "192x192"}]},
                {"name": "Mi carrito", "short_name": "Carrito", "description": "Ver pedido actual",    "url": "/carrito", "icons": [{"src": "/static/pwa-icon-192.png", "sizes": "192x192"}]},
                {"name": "Mis puntos", "short_name": "Puntos",  "description": "Club de fidelizacion", "url": "/club",    "icons": [{"src": "/static/pwa-icon-192.png", "sizes": "192x192"}]},
            ],
            "screenshots": [
                {"src": "/static/pwa-screenshot-mobile.png", "sizes": "390x844", "type": "image/png", "form_factor": "narrow", "label": f"{nombre} — Menu online"},
                {"src": "/static/pwa-screenshot-wide.png", "sizes": "1280x720", "type": "image/png", "form_factor": "wide", "label": f"{nombre} — Pedidos y seguimiento"},
            ],
            "prefer_related_applications": False,
            "edge_side_panel": {"preferred_width": 400},
        }
        manifest["icons"].extend([
            {"src": "/static/pwa-icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/pwa-icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/pwa-icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            {"src": "/static/pwa-icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
        ])
        response = app.response_class(
            json.dumps(manifest, ensure_ascii=False),
            mimetype="application/manifest+json",
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.route("/health/live")
    def health_live():
        return {"status": "ok"}, 200

    @app.route("/health")
    @app.route("/health/ready")
    def health_ready():
        try:
            db.session.execute(text("SELECT 1"))
            return {"status": "ok", "db": "ok"}, 200
        except Exception:
            db.session.rollback()
            app.logger.exception("health_check: DB no disponible")
            return {"status": "error", "db": "unreachable"}, 503

    @app.route("/webhook/evolution", methods=["POST"])
    @csrf.exempt
    @limiter.limit(os.environ.get("WEBHOOK_RATE_LIMIT", "120 per minute")) if limiter is not None else (lambda f: f)
    def evolution_webhook_proxy():
        """Entrada única del webhook Evolution; Oxidian lo entrega al bot interno."""
        from models import SiteConfig
        import requests

        if request.content_length and request.content_length > int(os.environ.get("WEBHOOK_MAX_BYTES", 512 * 1024)):
            return {"ok": False, "error": "payload too large"}, 413

        expected_secret = (
            SiteConfig.get("WEBHOOK_SECRET", "")
            or os.environ.get("WEBHOOK_SECRET", "")
        ).strip()
        provided_secret = (
            request.headers.get("X-Webhook-Secret")
            or request.headers.get("X-API-Key")
            or ""
        ).strip()
        if not expected_secret:
            app.logger.error("WEBHOOK_SECRET no configurado; rechazando webhook Evolution.")
            return {"ok": False, "error": "webhook secret not configured"}, 503
        if not provided_secret or not hmac.compare_digest(provided_secret, expected_secret):
            return {"ok": False, "error": "invalid webhook secret"}, 403

        bot_url = (SiteConfig.get("BOT_API_URL", os.environ.get("BOT_API_URL", "http://127.0.0.1:3000")) or "").rstrip("/")
        if not bot_url:
            return {"ok": False, "error": "BOT_API_URL not configured"}, 503

        headers = {
            "Content-Type": request.headers.get("Content-Type", "application/json"),
        }
        if provided_secret:
            headers["X-Webhook-Secret"] = provided_secret

        try:
            resp = requests.post(
                f"{bot_url}/webhook/evolution",
                data=request.get_data(),
                headers=headers,
                timeout=8,
            )
            return app.response_class(
                resp.content,
                status=resp.status_code,
                mimetype=resp.headers.get("content-type", "application/json"),
            )
        except requests.exceptions.RequestException as exc:
            app.logger.warning(
                "Webhook Evolution recibido, pero el bot no esta disponible en %s: %s",
                bot_url,
                exc,
            )
            return {"ok": False, "error": "bot_unavailable"}, 502

    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": generate_csrf}

    @app.context_processor
    def inject_constants():
        from models import ALERGENOS_EU
        return {
            "ALERGENOS_EU": ALERGENOS_EU,
            "asset_version": app.config["ASSET_VERSION"],
            "now": datetime.now,
        }

    @app.template_filter("time_ago")
    def time_ago_filter(dt):
        if not dt:
            return "—"
        diff = int((datetime.now(timezone.utc).replace(tzinfo=None) - dt).total_seconds())
        if diff < 60:
            return f"{diff}s"
        if diff < 3600:
            return f"{diff // 60}min"
        return f"{diff // 3600}h {(diff % 3600) // 60}min"

    @app.template_filter("upload_url")
    def upload_url_filter(path):
        """Devuelve una URL de imagen usable para rutas relativas, /uploads o URLs externas."""
        value = (path or "").strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://", "/")):
            return value
        if value.startswith("uploads/"):
            return "/" + value
        return f"/uploads/{value}"

    @app.context_processor
    def inject_branding_config():
        from models import SiteConfig

        # Carga todas las claves de branding en una sola query y cachea en g
        # para no repetir 10+ SELECTs por request.
        if not hasattr(g, "_brand_cache"):
            _BRAND_KEYS = {
                "NOMBRE_NEGOCIO", "LOGO_URL", "TELEFONO_NEGOCIO",
                "DIRECCION_NEGOCIO", "CIUDAD_NEGOCIO",
                "COLOR_PRIMARIO", "COLOR_SECUNDARIO", "COLOR_ACENTO",
                "HORARIO_APERTURA", "HORARIO_CIERRE", "TIENDA_FORZAR_CERRADA",
                "TIENDA_MENSAJE_CIERRE",
                "APP_ICON_URL", "HERO_IMAGE_URL",
                "SLOGAN_NEGOCIO", "DESCRIPCION_NEGOCIO",
            }
            try:
                rows = SiteConfig.query.filter(SiteConfig.clave.in_(_BRAND_KEYS)).all()
                g._brand_cache = {r.clave: r.valor for r in rows}
            except Exception:
                app.logger.exception("No se pudo cargar SiteConfig para branding")
                g._brand_cache = {}

        cfg = g._brand_cache
        def _c(k, default=""): return cfg.get(k) or default

        nombre      = _c("NOMBRE_NEGOCIO", _env_default("NOMBRE_NEGOCIO", "Oxidian"))
        logo_url     = _c("LOGO_URL")
        app_icon_url = _c("APP_ICON_URL")
        hero_image_url = _c("HERO_IMAGE_URL")
        telefono  = _c("TELEFONO_NEGOCIO") or _env_default("OWNER_NUMBER", "")
        direccion = _c("DIRECCION_NEGOCIO", _env_default("DIRECCION_NEGOCIO", ""))
        ciudad    = _c("CIUDAD_NEGOCIO") or (direccion.split(",")[0].strip() if direccion else "")

        color_primario   = _c("COLOR_PRIMARIO",   _env_default("COLOR_PRIMARIO", "#FCD116"))
        color_secundario = _c("COLOR_SECUNDARIO", _env_default("COLOR_SECUNDARIO", "#CE1126"))
        color_acento     = _c("COLOR_ACENTO",     _env_default("COLOR_ACENTO", "#003087"))

        horario_apertura      = _c("HORARIO_APERTURA", _env_default("HORARIO_APERTURA", "09:00"))
        horario_cierre        = _c("HORARIO_CIERRE",   _env_default("HORARIO_CIERRE", "22:30"))
        tienda_mensaje_cierre = _c("TIENDA_MENSAJE_CIERRE", "")
        tienda_forzada_cerrada = _to_bool(_c("TIENDA_FORZAR_CERRADA", "0"), False)

        ahora = datetime.now().strftime("%H:%M")
        from services import tienda_abierta_en_horario
        tienda_abierta = tienda_abierta_en_horario(
            horario_apertura,
            horario_cierre,
            ahora=ahora,
            forzada_cerrada=tienda_forzada_cerrada,
        )

        slogan      = _c("SLOGAN_NEGOCIO", "")
        descripcion = _c("DESCRIPCION_NEGOCIO", "")

        return {
            "brand": {
                "nombre": nombre,
                "logo_url": logo_url,
                "app_icon_url": app_icon_url,
                "hero_image_url": hero_image_url,
                "telefono": telefono,
                "direccion": direccion,
                "ciudad": ciudad,
                "slogan": slogan,
                "descripcion": descripcion,
                "color_primario": color_primario,
                "color_secundario": color_secundario,
                "color_acento": color_acento,
                "horario_apertura": horario_apertura,
                "horario_cierre": horario_cierre,
                "tienda_mensaje_cierre": tienda_mensaje_cierre,
                "tienda_abierta": tienda_abierta,
            }
        }

    # Blueprints
    from routes.auth import auth_bp
    from routes.public import public_bp
    from routes.admin import admin_bp
    from routes.preparador import preparador_bp
    from routes.repartidor import repartidor_bp
    from routes.pos import pos_bp
    from routes.presencia import presencia_bp
    from routes.superadmin import superadmin_bp
    from routes.api_bot import api_bot_bp
    from routes.uploads import uploads_bp
    from routes.marketing import marketing_bp
    from routes.staff import staff_bp
    from routes.push import push_bp
    from routes.proveedor import proveedor_bp

    csrf.exempt(api_bot_bp)
    if limiter is not None:
        limiter.exempt(api_bot_bp)

    app.register_blueprint(auth_bp,        url_prefix="/auth")
    app.register_blueprint(public_bp,      url_prefix="/")
    app.register_blueprint(admin_bp,       url_prefix="/admin")
    app.register_blueprint(preparador_bp,  url_prefix="/preparador")
    app.register_blueprint(repartidor_bp,  url_prefix="/repartidor")
    app.register_blueprint(pos_bp,         url_prefix="/pos")
    app.register_blueprint(presencia_bp,   url_prefix="/api")
    app.register_blueprint(superadmin_bp,  url_prefix="/superadmin")
    app.register_blueprint(api_bot_bp,     url_prefix="/api/bot")
    app.register_blueprint(uploads_bp)
    app.register_blueprint(marketing_bp,   url_prefix="/marketing")
    app.register_blueprint(staff_bp,       url_prefix="/staff")
    app.register_blueprint(push_bp,        url_prefix="/api/push")
    app.register_blueprint(proveedor_bp,   url_prefix="/proveedor")

    # ── Páginas de error personalizadas ──
    @app.errorhandler(404)
    def page_not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        return render_template("errors/500.html"), 500

    # ── Cabeceras de seguridad ──
    @app.after_request
    def set_security_headers(response):
        duration = round((time.time() - getattr(g, "start_time", time.time())) * 1000, 2)
        if not request.path.startswith("/static"):
            app.logger.info("%s %s -> %s (%sms)", request.method, request.path, response.status_code, duration)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Origin-Agent-Cluster"] = "?1"
        response.headers["Content-Security-Policy"] = "; ".join((
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'self'",
            "form-action 'self'",
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' data: https://fonts.gstatic.com",
            "img-src 'self' data: blob: https:",
            "connect-src 'self'",
            "manifest-src 'self'",
            "worker-src 'self' blob:",
        ))
        if request.is_secure and app.config.get("SESSION_COOKIE_SECURE"):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        response.headers["X-Oxidian-Version"] = app.config["ASSET_VERSION"]
        if request.path in ("/health", "/health/live", "/health/ready", "/manifest.webmanifest", "/sw.js"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        sensitive_public_paths = (
            "/carrito",
            "/checkout",
            "/pedido/",
            "/perfil",
            "/puntos/",
        )
        if request.path.startswith(sensitive_public_paths):
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    if not _to_bool(os.environ.get("OXIDIAN_SKIP_STARTUP_DB"), False):
        with app.app_context():
            db.create_all()
            _seed_admin()
            _seed_operational_basics()
            _seed_demo_data()
            _seed_vapid_keys(app)

    return app


def _validar_config_runtime(app, env):
    """Falla pronto en producción si faltan variables críticas."""
    entorno = env or os.environ.get("FLASK_ENV", "development")
    if entorno != "production":
        return
    requeridas = ["SECRET_KEY", "DATABASE_URL", "BOT_API_KEY"]
    faltantes = [k for k in requeridas if not os.environ.get(k) and not app.config.get(k)]
    if faltantes:
        raise RuntimeError(f"Variables requeridas no configuradas: {faltantes}")
    if app.config.get("SECRET_KEY") in (None, "", "dev-key", "insecure"):
        raise RuntimeError("SECRET_KEY no puede ser el valor por defecto en producción")
    public_url = (os.environ.get("OXIDIAN_PUBLIC_URL") or "").strip().lower()
    if public_url.startswith("https://") and not app.config.get("SESSION_COOKIE_SECURE"):
        raise RuntimeError(
            "SESSION_COOKIE_SECURE debe estar activo cuando OXIDIAN_PUBLIC_URL usa HTTPS"
        )


def _seed_admin():
    import uuid
    from models import User, SiteConfig
    changed = False

    # Contraseña de seed configurable por variable de entorno
    seed_pw = _seed_password()
    flask_env = os.environ.get("FLASK_ENV", "development")
    if not os.environ.get("SEED_PASSWORD") and flask_env != "development":
        import warnings
        warnings.warn(
            "SEED_PASSWORD no esta configurada. Define una variable fuerte antes de arrancar en producción.",
            RuntimeWarning,
            stacklevel=2,
        )

    # Emails configurables: permite cambiarlos sin tocar el código en producción
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@oxidian.com")
    sa_email = os.environ.get("SUPERADMIN_EMAIL", "superadmin@oxidian.com")

    existing_sa = User.query.filter_by(email=sa_email).first()
    if existing_sa:
        # Si el usuario ya existe pero con rol inferior, promover a super_admin
        if existing_sa.rol != "super_admin":
            existing_sa.rol = "super_admin"
            changed = True
    else:
        sa = User(nombre="Super Admin", email=sa_email, rol="super_admin")
        sa.set_password(seed_pw)
        db.session.add(sa)
        changed = True

    # Solo crear admin separado si el email es distinto al de super_admin
    if admin_email != sa_email and not User.query.filter_by(email=admin_email).first():
        admin = User(nombre="Admin", email=admin_email, rol="admin")
        admin.set_password(seed_pw)
        db.session.add(admin)
        changed = True

    _defaults = [
        ("PUNTOS_POR_EURO",       "1",                        "Puntos por euro gastado"),
        ("PUNTOS_CANJE_RATIO",    "100",                      "100 puntos = 1 euro de descuento"),
        ("CART_MAX_QTY",          "99",                       "Cantidad máxima por producto en carrito"),
        ("COMBO_MIN_COMPONENTS",  "1",                        "Mínimo de componentes requeridos para crear un combo"),
        ("COMBO_MAX_COMPONENTS",  "30",                       "Máximo de componentes permitidos por combo"),
        ("COMBO_MAX_QTY_COMPONENT", "50",                     "Cantidad máxima por componente dentro de un combo"),
        ("COMBO_MAX_SELECTIONS_GROUP", "10",                  "Máximo de selecciones permitidas por grupo elegible"),
        ("COMBO_MAX_DISCOUNT_PCT", "50",                      "Descuento porcentual máximo permitido para combos"),
        ("ALERTA_CADUCIDAD_DIAS", "7",                        "Dias antes de caducidad para alerta"),
        ("NOMBRE_NEGOCIO",        _env_default("NOMBRE_NEGOCIO", "Oxidian"), "Nombre del negocio"),
        ("DIRECCION_NEGOCIO",     _env_default("DIRECCION_NEGOCIO", "Carmona, Sevilla"), "Direccion del local"),
        ("TELEFONO_NEGOCIO",      _env_default("TELEFONO_NEGOCIO", ""),      "Telefono de contacto"),
        ("BOT_API_KEY",           _env_default("BOT_API_KEY", str(uuid.uuid4())), "API key compartida entre Oxidian y el bot"),
        ("BOT_API_URL",           _env_default("BOT_API_URL", "http://127.0.0.1:3000"), "URL interna del bot WhatsApp"),
        ("BOT_PANEL_KEY",         _env_default("BOT_PANEL_KEY", ""), "Clave para administrar el bot desde Super Admin"),
        ("BOT_ALLOW_ORDER_CREATE", _env_default("BOT_ALLOW_ORDER_CREATE", "0"), "Permitir crear pedidos desde chatbot (1/0). Por defecto el bot solo consulta."),
        ("OXIDIAN_PUBLIC_URL",    "",                         "URL de Oxidian que usará el bot"),
        ("EVOLUTION_API_URL",     _env_default("EVOLUTION_API_URL", "http://127.0.0.1:8080"), "URL interna de Evolution API"),
        ("EVOLUTION_API_KEY",     _env_default("EVOLUTION_API_KEY", ""), "Clave API de Evolution"),
        ("EVOLUTION_INSTANCE",    _env_default("EVOLUTION_INSTANCE", "oxidian"), "Instancia WhatsApp de Evolution"),
        ("WEBHOOK_SECRET",        _env_default("WEBHOOK_SECRET", ""), "Secreto webhook Evolution -> Oxidian"),
        ("BOT_EMAIL_DOMAIN",      "wa.internal",              "Dominio para emails auto-generados de clientes WhatsApp"),
        ("LOGO_URL",              _env_default("LOGO_URL", ""),              "URL del logo del negocio"),
        ("TIENDA_URL",            _env_default("TIENDA_URL", ""),            "URL de la tienda web para pedidos (mostrado en WhatsApp)"),
        ("COLOR_PRIMARIO",        _env_default("COLOR_PRIMARIO", "#E8B26C"), "Color principal de marca"),
        ("COLOR_SECUNDARIO",      _env_default("COLOR_SECUNDARIO", "#D65A2A"), "Color secundario de marca"),
        ("COLOR_ACENTO",          _env_default("COLOR_ACENTO", "#6B3D8A"),   "Color de acento para estado y CTA"),
        ("HORARIO_APERTURA",      _env_default("HORARIO_APERTURA", "09:00"), "Hora de apertura tienda (HH:MM)"),
        ("HORARIO_CIERRE",        _env_default("HORARIO_CIERRE", "22:30"),   "Hora de cierre tienda (HH:MM)"),
        ("TIENDA_FORZAR_CERRADA", "0",                        "Forzar tienda cerrada (1/0)"),
        # Geo-validación de radio de entrega
        ("CIUDAD_NEGOCIO",                    _env_default("CIUDAD_NEGOCIO", "Carmona"),  "Ciudad del negocio (para geocodificación de direcciones)"),
        ("PROVINCIA_NEGOCIO",                 _env_default("PROVINCIA_NEGOCIO", "Sevilla"), "Provincia del negocio (para geocodificación estructurada)"),
        ("CENTRO_LAT",                        _env_default("CENTRO_LAT", "37.4698"),      "Latitud del centro de reparto"),
        ("CENTRO_LON",                        _env_default("CENTRO_LON", "-5.6435"),      "Longitud del centro de reparto"),
        ("RADIO_ENTREGA_KM",                  _env_default("RADIO_ENTREGA_KM", "2"),      "Radio máximo de entrega en km desde el centro"),
        ("VALIDAR_RADIO_ENTREGA",             "1",          "Activar validación de radio de entrega (1/0)"),
        ("BLOQUEAR_DIRECCION_NO_VERIFICADA",  "1",          "Bloquear pedido si no se puede geocodificar la dirección (1/0)"),
    ]
    for clave, valor, desc in _defaults:
        if not SiteConfig.query.filter_by(clave=clave).first():
            SiteConfig.set(clave, valor, descripcion=desc)
            changed = True

    if changed:
        db.session.commit()


def _seed_operational_basics():
    """Crea solo datos necesarios para operar, sin clientes ni pedidos demo."""
    from models import AdminFeature, ADMIN_FEATURES, SiteConfig, User, ZonaEntrega, utcnow

    changed = False
    seed_pw = _seed_password()
    now = utcnow()
    minimal_users = _to_bool(os.environ.get("OXIDIAN_MINIMAL_USERS"), False)

    if minimal_users:
        staff_users = [
            {"nombre": "Armado de Pedidos", "email": "preparacion@oxidian.com", "rol": "preparacion", "puesto_trabajo": "Armado de pedidos delivery"},
            {"nombre": "Repartidor Delivery", "email": "repartidor@oxidian.com", "rol": "repartidor", "puesto_trabajo": "Reparto delivery", "tarifa_entrega": 2.5},
        ]
    else:
        staff_users = [
            {"nombre": "Chef Carlos", "email": "cocina@oxidian.com", "rol": "cocina", "puesto_trabajo": "Cocina"},
            {"nombre": "Prep María", "email": "preparacion@oxidian.com", "rol": "preparacion", "puesto_trabajo": "Preparación"},
            {"nombre": "Ana Staff", "email": "staff@oxidian.com", "rol": "staff", "puesto_trabajo": "Caja e inventario"},
            {"nombre": "Pedro Delivery", "email": "repartidor@oxidian.com", "rol": "repartidor", "puesto_trabajo": "Reparto", "tarifa_entrega": 2.5},
        ]
    for payload in staff_users:
        user = User.query.filter_by(email=payload["email"]).first()
        if not user:
            user = User(
                nombre=payload["nombre"],
                email=payload["email"],
                rol=payload["rol"],
                puesto_trabajo=payload.get("puesto_trabajo"),
                tarifa_entrega=payload.get("tarifa_entrega", 0),
                activo=True,
                last_seen=now,
                en_linea=False,
            )
            user.set_password(seed_pw)
            db.session.add(user)
            changed = True
        else:
            if user.rol != payload["rol"]:
                user.rol = payload["rol"]
                changed = True
            if user.nombre != payload["nombre"]:
                user.nombre = payload["nombre"]
                changed = True
            if user.puesto_trabajo != payload.get("puesto_trabajo"):
                user.puesto_trabajo = payload.get("puesto_trabajo")
                changed = True
            if float(user.tarifa_entrega or 0) != float(payload.get("tarifa_entrega", 0)):
                user.tarifa_entrega = payload.get("tarifa_entrega", 0)
                changed = True
            if user.en_linea:
                user.en_linea = False
                changed = True
            if not user.activo:
                user.activo = True
                changed = True

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@oxidian.com")
    admin_user = User.query.filter_by(email=admin_email).first()
    if admin_user:
        for feat in ADMIN_FEATURES:
            if not AdminFeature.query.filter_by(user_id=admin_user.id, feature=feat).first():
                db.session.add(AdminFeature(user_id=admin_user.id, feature=feat, activo=True))
                changed = True

    if not minimal_users and not ZonaEntrega.query.first():
        db.session.add(ZonaEntrega(
            nombre="Carmona Centro",
            descripcion="Zona principal de entrega",
            es_epicentro=True,
            activo=True,
            precio_envio=0,
            tiempo_estimado_min=25,
            gratis_desde=20,
            orden=1,
        ))
        changed = True

    if not SiteConfig.get("TIENDA_URL"):
        SiteConfig.set("TIENDA_URL", "", descripcion="URL publica de la tienda")
        changed = True

    if changed:
        db.session.commit()


def _seed_demo_data():
    """
    Datos de demo ricos: 5 roles, 5 categorías, 15 productos con imágenes, 3 combos,
    zona de entrega, cupones, pedidos demo y reseñas. Idempotente — no inserta duplicados.
    """
    if not _to_bool(os.environ.get("OXIDIAN_SEED_DEMO"), False):
        return

    from models import (Categoria, Coupon, Product, ComboItem, Stock,
                        User, ZonaEntrega, Order, OrderItem, Caja,
                        AdminFeature, utcnow)

    # Imagen base Unsplash (w=400, crop, q=80)
    U = "https://images.unsplash.com/photo-{}?w=400&fit=crop&q=80"

    changed = False
    now = utcnow()
    seed_pw = _seed_password()

    # ── Usuarios operativos ────────────────────────────────────────
    demo_users = [
        {"nombre": "Chef Carlos",    "email": "cocina@oxidian.com",       "rol": "cocina",      "puesto_trabajo": "Jefe de Cocina"},
        {"nombre": "Prep María",     "email": "preparacion@oxidian.com",  "rol": "preparacion", "puesto_trabajo": "Gestión Encargos"},
        {"nombre": "Ana Staff",      "email": "staff@oxidian.com",        "rol": "staff",       "puesto_trabajo": "Inventario & Caja"},
        {"nombre": "Pedro Delivery", "email": "repartidor@oxidian.com",   "rol": "repartidor",  "tarifa_entrega": 2.5},
        {"nombre": "Laura Sánchez",  "email": "cliente@oxidian.com",      "rol": "cliente",     "telefono": "600000001", "puntos": 250},
        {"nombre": "Carlos Reyes",   "email": "cliente2@oxidian.com",     "rol": "cliente",     "telefono": "600000002", "puntos": 80},
    ]
    for p in demo_users:
        if not User.query.filter_by(email=p["email"]).first():
            u = User(
                nombre=p["nombre"], email=p["email"], rol=p["rol"],
                puesto_trabajo=p.get("puesto_trabajo"),
                tarifa_entrega=p.get("tarifa_entrega", 0),
                telefono=p.get("telefono"),
                puntos=p.get("puntos", 0),
                activo=True, last_seen=now,
            )
            u.set_password(seed_pw)
            db.session.add(u)
            changed = True

    # ── Categorías ────────────────────────────────────────────────
    cat_data = [
        ("Combos",          "Los mejores combos de la casa",             1,  "https://images.unsplash.com/photo-1504674900247-0877df9cc836?w=200&fit=crop"),
        ("Fritos & Snacks", "Empanadas, tequeños, buñuelos y más",       2,  "https://images.unsplash.com/photo-1601050690597-df0568f70950?w=200&fit=crop"),
        ("Arepas & Platos", "Platos típicos colombianos y venezolanos",  3,  None),
        ("Postres",         "Dulces y postres latinos tradicionales",    4,  None),
        ("Bebidas",         "Refrescos, jugos y aguas",                  5,  "https://images.unsplash.com/photo-1544145945-f90425340c7e?w=200&fit=crop"),
    ]
    for nombre, desc, orden, img in cat_data:
        if not Categoria.query.filter_by(nombre=nombre).first():
            c = Categoria(nombre=nombre, descripcion=desc, orden=orden)
            if img:
                c.imagen_url = img
            db.session.add(c)
            changed = True

    db.session.flush()

    def cid(n):
        c = Categoria.query.filter_by(nombre=n).first()
        return c.id if c else None

    # ── Productos ─────────────────────────────────────────────────
    # Formato: nombre, desc, precio, costo, cat, stock, tipo_entrega, kwargs
    # kwargs: imagen_url, origen_pais, alergenos_info, alergenos_json,
    #         es_hipoalergenico, canjeable_con_puntos, puntos_para_canje, es_combo,
    #         dias_anticipacion_encargo, stock_mostrar_en_web
    demo_products = [
        # ── Fritos ──
        ("Empanada de Pollo",
         "Empanada frita crujiente rellena de pollo guisado y papa criolla. Receta venezolana auténtica.",
         2.50, 1.00, "Fritos & Snacks", 60, "inmediato",
         {"imagen_url": U.format("1601050690597-df0568f70950"),
          "origen_pais": "Venezuela",
          "alergenos_info": "Gluten, Huevos",
          "stock_mostrar_en_web": True}),

        ("Empanada de Carne",
         "Empanada de carne molida sazonada con especias colombianas. Perfecta como snack.",
         2.80, 1.10, "Fritos & Snacks", 50, "inmediato",
         {"imagen_url": U.format("1432139509613-5c4255815697"),
          "origen_pais": "Colombia",
          "alergenos_info": "Gluten, Huevos"}),

        ("Tequeños de Queso (4 uds)",
         "Palitos fritos de masa de maíz rellenos de queso blanco venezolano. Sin gluten.",
         4.50, 1.80, "Fritos & Snacks", 35, "inmediato",
         {"imagen_url": U.format("1565958011703-44f9829ba187"),
          "origen_pais": "Venezuela",
          "es_hipoalergenico": True,
          "canjeable_con_puntos": True, "puntos_para_canje": 200}),

        ("Buñuelos de Queso (6 uds)",
         "Bolitas fritas de masa de maíz con queso derretido por dentro. Dulces y crujientes.",
         3.50, 1.40, "Fritos & Snacks", 40, "inmediato",
         {"imagen_url": U.format("1484723091739-30acba47f059"),
          "origen_pais": "Colombia",
          "alergenos_info": "Gluten, Huevos, Lácteos"}),

        ("Papas Chorreadas",
         "Papas cocidas con salsa de hogao y queso. Un clásico de la cocina bogotana.",
         3.90, 1.50, "Fritos & Snacks", 30, "inmediato",
         {"imagen_url": U.format("1546069901-ba9599a7e63c"),
          "origen_pais": "Colombia",
          "alergenos_info": "Lácteos"}),

        # ── Arepas & Platos ──
        ("Arepa Reina Pepiada",
         "Arepa blanca de maíz rellena con aguacate, pollo desmenuzado y mayonesa. Producto programado.",
         4.90, 2.20, "Arepas & Platos", 20, "programado",
         {"imagen_url": U.format("1571105023408-52f8b9e78b06"),
          "origen_pais": "Venezuela",
          "alergenos_info": "Huevos",
          "fecha_llegada": date.today() + timedelta(days=2)}),

        ("Bandeja Paisa",
         "El plato más representativo de Colombia: fríjoles, chicharrón, chorizo, arroz, huevo frito, aguacate y arepa.",
         10.90, 5.00, "Arepas & Platos", 15, "programado",
         {"imagen_url": U.format("1565299585323-38d6b0865b47"),
          "origen_pais": "Colombia",
          "alergenos_info": "Gluten, Huevos, Soja",
          "fecha_llegada": date.today() + timedelta(days=2)}),

        ("Sancocho de Pollo",
         "Sopa reconfortante con pollo, yuca, papa, plátano y mazorca. Para compartir.",
         8.50, 3.80, "Arepas & Platos", 10, "programado",
         {"imagen_url": U.format("1547592166-23ac45744acd"),
          "origen_pais": "Colombia",
          "alergenos_info": "Gluten",
          "fecha_llegada": date.today() + timedelta(days=3)}),

        # ── Postres ──
        ("Natilla Colombiana",
         "Postre tradicional de leche, panela y canela. Cremoso y suave. Hecho en casa.",
         3.20, 1.20, "Postres", 25, "inmediato",
         {"imagen_url": U.format("1551024709-8f23befc548c"),
          "origen_pais": "Colombia",
          "alergenos_info": "Lácteos",
          "canjeable_con_puntos": True, "puntos_para_canje": 150}),

        ("Majarete de Coco",
         "Flan suave de maíz tierno con coco rallado y canela. Postre venezolano por excelencia.",
         2.80, 1.10, "Postres", 20, "inmediato",
         {"imagen_url": U.format("1578985545062-70e57660f7c2"),
          "origen_pais": "Venezuela",
          "alergenos_info": "Lácteos"}),

        # ── Bebidas ──
        ("Malta Colombiana 33cl",
         "Refresco de malta sin alcohol, oscura y dulce. El clásico acompañante de la fritanga.",
         2.00, 0.80, "Bebidas", 60, "inmediato",
         {"imagen_url": U.format("1554740420-7b8a3bdde6d2"),
          "origen_pais": "Colombia"}),

        ("Jugo Natural de Lulo",
         "Jugo fresco de lulo (naranjilla) con agua o leche. Fruta exótica colombiana.",
         3.00, 1.20, "Bebidas", 30, "inmediato",
         {"imagen_url": U.format("1490645935967-10de6ba17061"),
          "origen_pais": "Colombia",
          "alergenos_info": "Lácteos"}),

        ("Agua Mineral 33cl",
         "Agua mineral natural sin gas. Fría y refrescante.",
         1.00, 0.35, "Bebidas", 80, "inmediato",
         {"imagen_url": U.format("1548839140-29a749e1cf4d"),
          "es_hipoalergenico": True}),

        # ── Combos ──
        ("Combo Parcerito",
         "El favorito del barrio: 2 empanadas a elegir + 1 bebida a elegir. ¡Todo por menos!",
         6.50, 2.80, "Combos", 0, "inmediato",
         {"imagen_url": U.format("1550547660-d9450f859349"),
          "origen_pais": "Carmona",
          "es_combo": True}),

        ("Combo Familiar",
         "Para toda la familia: 6 empanadas surtidas + 2 bebidas a elegir + 2 postres.",
         18.50, 8.50, "Combos", 0, "inmediato",
         {"imagen_url": U.format("1504674900247-0877df9cc836"),
          "origen_pais": "Carmona",
          "es_combo": True}),

        ("Combo Arepa Feliz",
         "Arepa Reina Pepiada + Malta o Jugo Natural. El almuerzo perfecto. Producto programado.",
         7.50, 3.50, "Combos", 0, "programado",
         {"imagen_url": U.format("1546069901-ba9599a7e63c"),
          "origen_pais": "Carmona",
          "es_combo": True,
          "fecha_llegada": date.today() + timedelta(days=2)}),
    ]

    for (nombre, desc, precio, costo, cat_nombre, stock_qty,
         tipo_entrega, kwargs) in demo_products:
        if not Product.query.filter_by(nombre=nombre).first():
            is_combo = kwargs.pop("es_combo", False)
            imagen   = kwargs.pop("imagen_url", None)
            stock_k  = kwargs.pop("stock_mostrar_en_web", False)
            p = Product(
                nombre=nombre, descripcion=desc,
                precio=precio, precio_costo=costo,
                categoria_id=cid(cat_nombre),
                tipo_entrega=tipo_entrega,
                activo=True, es_combo=is_combo,
                stock_mostrar_en_web=stock_k,
                **kwargs,
            )
            if imagen:
                p.imagen_url = imagen
            db.session.add(p)
            db.session.flush()
            if not is_combo and stock_qty > 0:
                db.session.add(Stock(
                    producto_id=p.id, cantidad=stock_qty,
                    unidad="unidad", fecha_entrada=date.today(),
                    ubicacion="almacen", alerta_dias=7,
                ))
            changed = True

    db.session.flush()

    # ── Combo items ───────────────────────────────────────────────
    def pid(n):
        p = Product.query.filter_by(nombre=n).first()
        return p

    # Combo Parcerito: 2x empanada (seleccionable: pollo o carne) + 1 bebida (seleccionable)
    combo1 = pid("Combo Parcerito")
    emp_pol = pid("Empanada de Pollo")
    emp_car = pid("Empanada de Carne")
    malta   = pid("Malta Colombiana 33cl")
    agua    = pid("Agua Mineral 33cl")
    jugo    = pid("Jugo Natural de Lulo")
    if combo1 and not ComboItem.query.filter_by(combo_id=combo1.id).first():
        if emp_pol:
            db.session.add(ComboItem(combo_id=combo1.id, producto_id=emp_pol.id,
                                     cantidad=2, es_seleccionable=True,
                                     grupo_seleccion="Elige tu empanada", max_selecciones=1))
        if emp_car:
            db.session.add(ComboItem(combo_id=combo1.id, producto_id=emp_car.id,
                                     cantidad=2, es_seleccionable=True,
                                     grupo_seleccion="Elige tu empanada", max_selecciones=1))
        if malta:
            db.session.add(ComboItem(combo_id=combo1.id, producto_id=malta.id,
                                     cantidad=1, es_seleccionable=True,
                                     grupo_seleccion="Elige tu bebida", max_selecciones=1))
        if agua:
            db.session.add(ComboItem(combo_id=combo1.id, producto_id=agua.id,
                                     cantidad=1, es_seleccionable=True,
                                     grupo_seleccion="Elige tu bebida", max_selecciones=1))
        if jugo:
            db.session.add(ComboItem(combo_id=combo1.id, producto_id=jugo.id,
                                     cantidad=1, es_seleccionable=True,
                                     grupo_seleccion="Elige tu bebida", max_selecciones=1))
        changed = True

    # Combo Familiar: 6x empanada (seleccionable max 2) + 2 bebidas (seleccionable) + 2 postres fijos
    combo2 = pid("Combo Familiar")
    natilla = pid("Natilla Colombiana")
    majarete = pid("Majarete de Coco")
    teq = pid("Tequeños de Queso (4 uds)")
    if combo2 and not ComboItem.query.filter_by(combo_id=combo2.id).first():
        for ep in [emp_pol, emp_car, teq]:
            if ep:
                db.session.add(ComboItem(combo_id=combo2.id, producto_id=ep.id,
                                         cantidad=2, es_seleccionable=True,
                                         grupo_seleccion="Elige snacks (2)", max_selecciones=2))
        for beb in [malta, agua, jugo]:
            if beb:
                db.session.add(ComboItem(combo_id=combo2.id, producto_id=beb.id,
                                         cantidad=1, es_seleccionable=True,
                                         grupo_seleccion="Elige bebidas (2)", max_selecciones=2))
        if natilla:
            db.session.add(ComboItem(combo_id=combo2.id, producto_id=natilla.id,
                                     cantidad=2, es_seleccionable=False))
        changed = True

    # Combo Arepa Feliz: 1x arepa (fijo) + 1 bebida (seleccionable)
    combo3 = pid("Combo Arepa Feliz")
    arepa = pid("Arepa Reina Pepiada")
    if combo3 and not ComboItem.query.filter_by(combo_id=combo3.id).first():
        if arepa:
            db.session.add(ComboItem(combo_id=combo3.id, producto_id=arepa.id,
                                     cantidad=1, es_seleccionable=False))
        for beb in [malta, jugo]:
            if beb:
                db.session.add(ComboItem(combo_id=combo3.id, producto_id=beb.id,
                                         cantidad=1, es_seleccionable=True,
                                         grupo_seleccion="Elige tu bebida", max_selecciones=1))
        changed = True

    # ── Zona de entrega ───────────────────────────────────────────
    if not ZonaEntrega.query.filter_by(nombre="Carmona Centro").first():
        db.session.add(ZonaEntrega(
            nombre="Carmona Centro", descripcion="Casco histórico y área urbana",
            es_epicentro=True, activo=True,
            precio_envio=0, tiempo_estimado_min=20, gratis_desde=20, orden=1,
        ))
        changed = True
    if not ZonaEntrega.query.filter_by(nombre="Extrarradio Carmona").first():
        db.session.add(ZonaEntrega(
            nombre="Extrarradio Carmona", descripcion="Urbanizaciones y zonas periféricas",
            es_epicentro=False, activo=True,
            precio_envio=1.50, tiempo_estimado_min=35, gratis_desde=30, orden=2,
        ))
        changed = True

    # ── Cupones ───────────────────────────────────────────────────
    cupones = [
        ("PARCERITO10", "10% descuento bienvenida",     "porcentaje",  10.0, 5.0),
        ("FAMILIAR15",  "15% en combos familiares",     "porcentaje",  15.0, 15.0),
        ("ENVIOGRATIS", "Envío gratis en tu pedido",    "envio_gratis", 0.0,  8.0),
        ("PRIMERPEDIDO","5% en tu primer pedido online","porcentaje",   5.0,  0.0),
    ]
    from models import Coupon
    for codigo, desc, tipo, valor, minimo in cupones:
        if not Coupon.query.filter_by(codigo=codigo).first():
            db.session.add(Coupon(
                codigo=codigo, descripcion=desc, tipo=tipo,
                valor=valor, minimo_pedido=minimo, activo=True,
            ))
            changed = True

    # ── AdminFeature para admin ───────────────────────────────────
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@oxidian.com")
    admin_user = User.query.filter_by(email=admin_email).first()
    if admin_user:
        from models import ADMIN_FEATURES as _AF
        for feat in _AF:
            if not AdminFeature.query.filter_by(user_id=admin_user.id, feature=feat).first():
                db.session.add(AdminFeature(user_id=admin_user.id, feature=feat, activo=True))
                changed = True

    # ── Pedidos demo ─────────────────────────────────────────────
    if Order.query.count() == 0:
        from decimal import Decimal

        cliente1 = User.query.filter_by(email="cliente@oxidian.com").first()
        cliente2 = User.query.filter_by(email="cliente2@oxidian.com").first()
        zona = ZonaEntrega.query.filter_by(nombre="Carmona Centro").first()

        emp_pol  = Product.query.filter_by(nombre="Empanada de Pollo").first()
        emp_car  = Product.query.filter_by(nombre="Empanada de Carne").first()
        teq      = Product.query.filter_by(nombre="Tequeños de Queso (4 uds)").first()
        bunuelos = Product.query.filter_by(nombre="Buñuelos de Queso (6 uds)").first()
        malta    = Product.query.filter_by(nombre="Malta Colombiana 33cl").first()
        agua     = Product.query.filter_by(nombre="Agua Mineral 33cl").first()
        jugo     = Product.query.filter_by(nombre="Jugo Natural de Lulo").first()
        natilla  = Product.query.filter_by(nombre="Natilla Colombiana").first()
        majarete = Product.query.filter_by(nombre="Majarete de Coco").first()
        combo1   = Product.query.filter_by(nombre="Combo Parcerito").first()

        def _make_order(numero, cliente, estado, metodo, dias_atras, items_data, zona_obj=None):
            """Helper que construye un pedido + sus items + caja si entregado."""
            subtotal = Decimal("0")
            order = Order(
                numero_pedido=numero,
                cliente_id=cliente.id if cliente else 1,
                estado=estado,
                origen="online",
                subtotal=Decimal("0"),
                descuento=Decimal("0"),
                total=Decimal("0"),
                metodo_pago=metodo,
                direccion_entrega="Calle Mayor 1, Carmona",
                notas="Pedido de demostración",
                zona_id=zona_obj.id if zona_obj else None,
                creado_en=utcnow() - timedelta(days=dias_atras),
                entregado_en=(utcnow() - timedelta(days=dias_atras, hours=-1)) if estado == "entregado" else None,
                es_entrega_epicentro=True,
            )
            db.session.add(order)
            db.session.flush()

            for prod, qty in items_data:
                if not prod:
                    continue
                pu = Decimal(str(prod.precio_final))
                st = pu * qty
                subtotal += st
                db.session.add(OrderItem(
                    pedido_id=order.id,
                    producto_id=prod.id,
                    cantidad=qty,
                    precio_unit=pu,
                    subtotal=st,
                ))

            order.subtotal = subtotal
            order.total = subtotal
            db.session.flush()

            if estado == "entregado":
                cat = "venta_online"
                db.session.add(Caja(
                    tipo="ingreso",
                    categoria=cat,
                    monto=subtotal,
                    concepto=f"Venta pedido {numero}",
                    pedido_id=order.id,
                    fecha=order.creado_en,
                ))

            return order

        if cliente1 and cliente2:
            _make_order("OX-DEMO-001", cliente1, "entregado",  "efectivo", 6,
                        [(emp_pol, 2), (malta, 1)], zona)
            _make_order("OX-DEMO-002", cliente2, "entregado",  "bizum",    5,
                        [(teq, 1), (bunuelos, 1), (agua, 2)], zona)
            _make_order("OX-DEMO-003", cliente1, "entregado",  "efectivo", 4,
                        [(combo1, 1), (natilla, 1)], zona)
            _make_order("OX-DEMO-004", cliente2, "entregado",  "bizum",    3,
                        [(emp_car, 3), (jugo, 1), (majarete, 1)], zona)
            _make_order("OX-DEMO-005", cliente1, "entregado",  "efectivo", 2,
                        [(emp_pol, 1), (emp_car, 1), (agua, 1)], zona)
            _make_order("OX-DEMO-006", cliente2, "en_ruta",    "bizum",    0,
                        [(combo1, 1), (agua, 1)], zona)
            _make_order("OX-DEMO-007", cliente1, "armando",    "efectivo", 0,
                        [(teq, 2), (malta, 1)], zona)
            _make_order("OX-DEMO-008", cliente2, "pendiente",  "bizum",    0,
                        [(emp_pol, 4), (bunuelos, 1), (jugo, 2)], zona)
            _make_order("OX-DEMO-009", cliente1, "cancelado",  "efectivo", 1,
                        [(emp_car, 2), (agua, 1)], zona)
            _make_order("OX-DEMO-010", cliente2, "listo",      "bizum",    0,
                        [(natilla, 2), (majarete, 1), (malta, 1)], zona)

        changed = True

    if changed:
        db.session.commit()


if __name__ == "__main__":
    app = create_app("development")
    port = int(os.environ.get("OXIDIAN_PORT") or os.environ.get("FLASK_RUN_PORT") or "5055")
    host = os.environ.get("OXIDIAN_HOST", "0.0.0.0")
    debug = _to_bool(os.environ.get("FLASK_DEBUG"), True)
    app.run(host=host, port=port, debug=debug, use_reloader=False)
