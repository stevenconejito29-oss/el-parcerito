import json
import hashlib
import logging
import os
import secrets
import time
import hmac
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, render_template, request, send_from_directory, g
from flask_wtf.csrf import generate_csrf
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from config import config
from extensions import db, login_manager, csrf, limiter
from flask_login import current_user, login_required
from store_config import BRAND_COLOR_DEFAULTS, PUBLIC_THEME_DEFAULTS, PUBLIC_UI_DEFAULTS


def _to_bool(val, default=False):
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "si", "sí", "on", "yes")


def _env_default(key, fallback=""):
    return os.environ.get(key, fallback)


def _seed_password():
    return os.environ.get("SEED_PASSWORD") or secrets.token_urlsafe(24)


def _asset_version(app):
    """Huella de todos los CSS/JS para invalidar cachés en cada cambio real.

    La lista no se mantiene a mano: cualquier componente nuevo dentro de
    ``static/css`` o ``static/js`` participa automáticamente en la versión.
    Incluir la ruta evita colisiones entre concatenaciones de archivos.
    """
    digest = hashlib.sha256()
    static_root = Path(app.static_folder)
    assets = []
    for directory, suffix in (("css", ".css"), ("js", ".js")):
        assets.extend((static_root / directory).rglob(f"*{suffix}"))
    assets.append(static_root / "sw.js")

    for path in sorted(assets, key=lambda item: item.as_posix()):
        relative_path = path.relative_to(static_root).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        try:
            with path.open("rb") as asset:
                for chunk in iter(lambda: asset.read(64 * 1024), b""):
                    digest.update(chunk)
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
        from store_config import get_store_profile

        profile = get_store_profile()
        nombre = profile["nombre"]
        short_name = (nombre[:12] or "Mi tienda").strip()
        _tt = (SiteConfig.get("TIPO_TIENDA", "comida") or "comida").lower()
        _es_comida = (_tt == "comida")
        _catalogo_word = "Menu" if _es_comida else "Catalogo"
        capabilities = []
        if profile["delivery"]:
            capabilities.append("entrega a domicilio")
        if profile["recogida"]:
            capabilities.append("recogida en local")
        if profile["pedidos_programados"]:
            capabilities.append("pedidos por fecha")
        capabilities_text = ", ".join(capabilities) if capabilities else "compra guiada"
        description = f"{_catalogo_word} online y carrito con {capabilities_text}."
        shortcuts = [
            {"name": f"Ver {_catalogo_word.lower()}", "short_name": _catalogo_word, "description": "Explora el catalogo", "url": "/", "icons": [{"src": "/static/pwa-icon-192.png", "sizes": "192x192"}]},
            {"name": "Mi carrito", "short_name": "Carrito", "description": "Ver pedido actual", "url": "/carrito", "icons": [{"src": "/static/pwa-icon-192.png", "sizes": "192x192"}]},
        ]
        if profile["puntos"]:
            shortcuts.append({"name": "Mis puntos", "short_name": "Puntos", "description": "Club de fidelizacion", "url": "/club", "icons": [{"src": "/static/pwa-icon-192.png", "sizes": "192x192"}]})

        # Background dinamico: dark si la marca tiene fondo oscuro (por defecto sí)
        brand_bg = SiteConfig.get("COLOR_FONDO_APP", "") or "#0F0906"
        theme_color = SiteConfig.get("COLOR_PRIMARIO", BRAND_COLOR_DEFAULTS["COLOR_PRIMARIO"])
        manifest = {
            "name": nombre,
            "short_name": short_name,
            "description": description,
            "start_url": "/?source=pwa",
            "scope": "/",
            "display": "standalone",
            "display_override": ["window-controls-overlay", "standalone", "minimal-ui", "browser"],
            "background_color": brand_bg,
            "theme_color": theme_color,
            "orientation": "any",
            "lang": "es",
            "dir": "ltr",
            "categories": (["food", "shopping", "lifestyle"] if _es_comida else ["shopping", "lifestyle", "business"]),
            "id": "oxidian-menu",
            "icons": [],
            "shortcuts": shortcuts,
            "screenshots": [
                {"src": "/static/pwa-screenshot-mobile.png", "sizes": "390x844", "type": "image/png", "form_factor": "narrow", "label": f"{nombre} — {_catalogo_word} online"},
                {"src": "/static/pwa-screenshot-wide.png", "sizes": "1280x720", "type": "image/png", "form_factor": "wide", "label": f"{nombre} — Pedidos y seguimiento"},
            ],
            "prefer_related_applications": False,
            "edge_side_panel": {"preferred_width": 400},
            # Handlers para deep-linking desde iOS/Android intents
            "launch_handler": {"client_mode": ["navigate-existing", "auto"]},
            "handle_links": "preferred",
            "protocol_handlers": [
                {"protocol": "web+order", "url": "/pedido/%s"}
            ],
            # Habilita compartir a la app desde el share sheet nativo
            "share_target": {
                "action": "/",
                "method": "GET",
                "params": {"title": "title", "text": "text", "url": "url"}
            },
        }
        if profile["app_icon_url"]:
            manifest["icons"].append({
                "src": profile["app_icon_url"], "sizes": "any", "purpose": "any maskable",
            })
        else:
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

    @app.route("/manifest-staff.webmanifest")
    @login_required
    def staff_web_manifest():
        from store_config import get_store_profile

        profile = get_store_profile()
        starts = {
            "super_admin": "/superadmin/dashboard",
            "admin": "/admin/dashboard",
            "preparacion": "/preparador/pedidos",
            "cocina": "/preparador/pedidos",
            "repartidor": "/repartidor/ruta",
        }
        icon = profile["app_icon_url"] or "/static/pwa-icon-192.png"
        manifest = {
            "name": f"{profile['nombre']} · Trabajo",
            "short_name": f"{profile['nombre'][:10]} Staff",
            "description": "Espacio de trabajo operativo por rol.",
            "id": f"oxidian-staff-{current_user.rol}",
            "start_url": starts.get(current_user.rol, "/auth/login"),
            "scope": "/",
            "display": "standalone",
            "background_color": "#111827",
            "theme_color": profile["color_primario"],
            "orientation": "any",
            "icons": [{"src": icon, "sizes": "any", "purpose": "any maskable"}],
        }
        return app.response_class(
            json.dumps(manifest, ensure_ascii=False),
            mimetype="application/manifest+json",
        )

    @app.route("/health/live")
    def health_live():
        return {"status": "ok"}, 200

    @app.route("/health")
    @app.route("/health/ready")
    def health_ready():
        checks = {"db": "unknown", "redis": "skipped", "outbox_stuck": 0}
        overall_ok = True
        try:
            db.session.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception:
            db.session.rollback()
            app.logger.exception("health_check: DB no disponible")
            checks["db"] = "unreachable"
            overall_ok = False
        # Redis (opcional): si REDIS_URL está definida, comprobamos ping. Si
        # está caída avisamos pero no marcamos 503 — la web funciona sin él,
        # solo pierde rate limiting y algún caché de sesión.
        redis_url = os.environ.get("REDIS_URL", "")
        if redis_url.startswith("redis://"):
            try:
                import redis as _redis  # type: ignore
                _r = _redis.Redis.from_url(redis_url, socket_connect_timeout=1.5, socket_timeout=1.5)
                checks["redis"] = "ok" if _r.ping() else "unreachable"
            except Exception:
                checks["redis"] = "unreachable"
        # Outbox atascado: mensajes pending con >1h sin procesar indican que
        # el worker de notificaciones no está corriendo o WhatsApp está caído.
        try:
            from models import NotificationOutbox
            hora_atras = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
            stuck = db.session.execute(
                text("SELECT COUNT(*) FROM notification_outbox "
                     "WHERE estado = 'pending' AND creado_en < :t"),
                {"t": hora_atras},
            ).scalar() or 0
            checks["outbox_stuck"] = int(stuck)
        except Exception:
            db.session.rollback()
        return checks, (200 if overall_ok else 503)

    @app.route("/health/integrations")
    def health_integrations():
        """Diagnóstico separado: una caída de WhatsApp no tumba la tienda web."""
        from models import SiteConfig
        import requests

        result = {"status": "ok", "bot": "unreachable", "evolution": "unreachable"}
        bot_url = SiteConfig.get("BOT_API_URL", os.environ.get("BOT_API_URL", "")).rstrip("/")
        evolution_url = SiteConfig.get(
            "EVOLUTION_API_URL", os.environ.get("EVOLUTION_API_URL", "")
        ).rstrip("/")
        try:
            response = requests.get(f"{bot_url}/health", timeout=3)
            bot_health = response.json() if response.ok else {}
            result["bot"] = "ok" if response.ok and bot_health.get("whatsapp_connected") is not False else "degraded"
            result["whatsapp"] = bot_health.get("evolution_state", "unknown")
        except requests.RequestException:
            result["bot"] = "unreachable"
        try:
            response = requests.get(evolution_url, timeout=3)
            result["evolution"] = "ok" if response.ok else "degraded"
        except requests.RequestException:
            result["evolution"] = "unreachable"
        if result["bot"] != "ok" or result["evolution"] != "ok":
            result["status"] = "degraded"
        return result, 200

    @app.route("/webhook/evolution", methods=["POST"])
    @csrf.exempt
    # Rate limit del webhook público. Antes: 120/min (demasiado laxo, un
    # atacante que descubra el endpoint y adivine WEBHOOK_SECRET podía
    # inundar el bot). Ahora: 60/min por IP — suficiente para conversaciones
    # reales (Evolution manda ~1 msg/s en pico) y bloquea flood explotable.
    # Configurable vía env `WEBHOOK_RATE_LIMIT` para ajustar por negocio.
    @limiter.limit(os.environ.get("WEBHOOK_RATE_LIMIT", "60 per minute")) if limiter is not None else (lambda f: f)
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

    @app.template_filter("from_json")
    def from_json_filter(value):
        """Parsea un string JSON a dict/list para usar en Jinja.
        Devuelve {} si es inválido o vacío. No lanza excepciones."""
        if not value:
            return {}
        if isinstance(value, (dict, list)):
            return value
        try:
            import json as _j
            return _j.loads(value) or {}
        except (TypeError, ValueError):
            return {}

    @app.template_filter("phone_digits")
    def phone_digits_filter(raw):
        """Extrae solo dígitos de un teléfono para wa.me / tel: URLs.
        Robusto ante paréntesis, puntos, espacios, guiones y símbolos.
        Reemplaza el patrón frágil `|replace(' ','')|replace('+','')|replace('-','')`
        que dejaba pasar caracteres no numéricos."""
        import re as _re
        return _re.sub(r"\D", "", str(raw or ""))

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
                "DIRECCION_NEGOCIO", "CIUDAD_NEGOCIO", "PROVINCIA_NEGOCIO",
                "PAIS_NEGOCIO", "EMAIL_CONTACTO", "BIZUM_TELEFONO",
                "BIZUM_HABILITADO", "EFECTIVO_HABILITADO",
                "MODO_TIENDA", "FEATURE_DELIVERY", "FEATURE_RECOGIDA",
                "FEATURE_PEDIDOS_PROGRAMADOS", "FEATURE_PUNTOS",
                "COLOR_PRIMARIO", "COLOR_SECUNDARIO", "COLOR_ACENTO",
                *PUBLIC_THEME_DEFAULTS.keys(), *PUBLIC_UI_DEFAULTS.keys(),
                "HORARIO_APERTURA", "HORARIO_CIERRE", "TIENDA_FORZAR_CERRADA",
                "TIENDA_MENSAJE_CIERRE",
                "APP_ICON_URL", "HERO_IMAGE_URL",
                "SLOGAN_NEGOCIO", "DESCRIPCION_NEGOCIO",
                "TIPO_TIENDA",
            }
            try:
                rows = SiteConfig.query.filter(SiteConfig.clave.in_(_BRAND_KEYS)).all()
                g._brand_cache = {r.clave: r.valor for r in rows}
            except Exception:
                app.logger.exception("No se pudo cargar SiteConfig para branding")
                g._brand_cache = {}

        cfg = g._brand_cache
        def _c(k, default=""): return cfg.get(k) or default

        nombre      = _c("NOMBRE_NEGOCIO", _env_default("NOMBRE_NEGOCIO", "Mi tienda"))
        logo_url     = _c("LOGO_URL")
        app_icon_url = _c("APP_ICON_URL")
        hero_image_url = _c("HERO_IMAGE_URL")
        telefono  = _c("TELEFONO_NEGOCIO") or _env_default("OWNER_NUMBER", "")
        direccion = _c("DIRECCION_NEGOCIO", _env_default("DIRECCION_NEGOCIO", ""))
        ciudad    = _c("CIUDAD_NEGOCIO") or (direccion.split(",")[0].strip() if direccion else "")
        provincia = _c("PROVINCIA_NEGOCIO")
        pais = _c("PAIS_NEGOCIO")
        email = _c("EMAIL_CONTACTO")
        bizum_telefono = _c("BIZUM_TELEFONO")

        color_primario   = _c("COLOR_PRIMARIO",   _env_default("COLOR_PRIMARIO", BRAND_COLOR_DEFAULTS["COLOR_PRIMARIO"]))
        color_secundario = _c("COLOR_SECUNDARIO", _env_default("COLOR_SECUNDARIO", BRAND_COLOR_DEFAULTS["COLOR_SECUNDARIO"]))
        color_acento     = _c("COLOR_ACENTO",     _env_default("COLOR_ACENTO", BRAND_COLOR_DEFAULTS["COLOR_ACENTO"]))
        theme = {
            key.removeprefix("COLOR_").lower(): _c(key, default)
            for key, default in PUBLIC_THEME_DEFAULTS.items()
        }
        ui = {
            key.removeprefix("UI_").lower(): _c(key, default)
            for key, default in PUBLIC_UI_DEFAULTS.items()
        }
        def _on_color(value):
            raw = str(value or "").lstrip("#")
            if len(raw) != 6:
                return "#FFFFFF"
            try:
                r, gr, b = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                return "#FFFFFF"
            luminance = (0.2126 * r + 0.7152 * gr + 0.0722 * b) / 255
            return "#18120A" if luminance > 0.58 else "#FFFFFF"

        horario_apertura      = _c("HORARIO_APERTURA", _env_default("HORARIO_APERTURA", "09:00"))
        horario_cierre        = _c("HORARIO_CIERRE",   _env_default("HORARIO_CIERRE", "22:30"))
        tienda_mensaje_cierre = _c("TIENDA_MENSAJE_CIERRE", "")
        tienda_forzada_cerrada = _to_bool(_c("TIENDA_FORZAR_CERRADA", "0"), False)
        tienda_forzada_abierta = _to_bool(_c("TIENDA_FORZAR_ABIERTA", "0"), False)
        modo_tienda = (_c("MODO_TIENDA", "propia") or "propia").strip().lower()
        if modo_tienda not in {"propia", "bar_servicio"}:
            modo_tienda = "propia"
        # TIPO_TIENDA determina la vertical: "comida" (default, retrocompat)
        # o "producto" (ropa, retail, cualquier catálogo genérico).
        # Afecta labels visibles ("menú"→"catálogo"), alérgenos (ocultos si
        # no es comida) y algún emoji por defecto. NUNCA cambia el modelo
        # ni rompe pedidos existentes.
        tipo_tienda = (_c("TIPO_TIENDA", "comida") or "comida").strip().lower()
        if tipo_tienda not in {"comida", "producto"}:
            tipo_tienda = "comida"
        feature_delivery = _to_bool(_c("FEATURE_DELIVERY", "1"), True)
        feature_recogida = _to_bool(_c("FEATURE_RECOGIDA", "1"), True)
        if not feature_delivery and not feature_recogida:
            feature_recogida = True

        ahora = datetime.now().strftime("%H:%M")
        from services import tienda_abierta_en_horario
        tienda_abierta = tienda_abierta_en_horario(
            horario_apertura,
            horario_cierre,
            ahora=ahora,
            forzada_cerrada=tienda_forzada_cerrada,
            forzada_abierta=tienda_forzada_abierta,
        )

        slogan      = _c("SLOGAN_NEGOCIO", "")
        descripcion = _c("DESCRIPCION_NEGOCIO", "")

        return {
            "ui": ui,
            "brand": {
                "nombre": nombre,
                "logo_url": logo_url,
                "app_icon_url": app_icon_url,
                "hero_image_url": hero_image_url,
                "telefono": telefono,
                "direccion": direccion,
                "ciudad": ciudad,
                "provincia": provincia,
                "pais": pais,
                "email": email,
                "bizum_telefono": bizum_telefono,
                "bizum_habilitado": _to_bool(_c("BIZUM_HABILITADO", "1"), True),
                "efectivo_habilitado": _to_bool(_c("EFECTIVO_HABILITADO", "1"), True),
                "modo_tienda": modo_tienda,
                "tipo_tienda": tipo_tienda,
                # Helpers explícitos para templates. Preferir estos flags a
                # comparar strings — más legible en Jinja.
                "es_comida": tipo_tienda == "comida",
                "es_producto": tipo_tienda == "producto",
                # Emoji vertical: comida = 🍽️, producto = 🛍️
                "vertical_emoji": "🍽️" if tipo_tienda == "comida" else "🛍️",
                # Label para "menú" según vertical
                "vertical_label": "Menú" if tipo_tienda == "comida" else "Catálogo",
                "delivery": feature_delivery,
                "recogida": feature_recogida,
                "pedidos_programados": _to_bool(_c("FEATURE_PEDIDOS_PROGRAMADOS", "1"), True),
                "puntos": _to_bool(_c("FEATURE_PUNTOS", "1"), True),
                "slogan": slogan,
                "descripcion": descripcion,
                "color_primario": color_primario,
                "on_primario": _on_color(color_primario),
                "color_secundario": color_secundario,
                "color_acento": color_acento,
                "on_acento": _on_color(color_acento),
                "theme": theme,
                "horario_apertura": horario_apertura,
                "horario_cierre": horario_cierre,
                "tienda_mensaje_cierre": tienda_mensaje_cierre,
                "tienda_abierta": tienda_abierta,
            }
        }

    @app.context_processor
    def inject_admin_feature_access():
        from models import SiteConfig
        tipo_tienda_context = (SiteConfig.get("TIPO_TIENDA", "comida") or "comida").strip().lower()

        def has_admin_feature(feature):
            if not current_user.is_authenticated:
                return False
            if current_user.rol == "super_admin":
                return True
            if current_user.rol != "admin":
                return False
            from models import AdminFeature
            return AdminFeature.tiene_acceso(current_user.id, feature)
        def role_label(role, short=False):
            es_comida = tipo_tienda_context == "comida"
            labels = {
                "super_admin": "Super Admin" if short else "Super administrador",
                "admin": "Admin" if short else "Administrador de tienda",
                "cocina": ("Cocina" if es_comida else "Almacén") if short else (
                    "Cocina · pedidos inmediatos" if es_comida else "Almacén · pedidos inmediatos"
                ),
                "preparacion": ("Encargos" if es_comida else "Preparación retail") if short else (
                    "Encargos con fecha" if es_comida else "Preparación de pedidos con fecha"
                ),
                "repartidor": "Repartidor",
                "cliente": "Cliente",
            }
            return labels.get(role, str(role or "").replace("_", " ").title())
        return {"has_admin_feature": has_admin_feature, "role_label": role_label}

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

    # ── CSP nonce por request ──
    # Se genera antes de cualquier render y se expone a Jinja como
    # `csp_nonce()`. Cada `<script>` inline debe incluir
    # `nonce="{{ csp_nonce() }}"`. El CSP declara solo scripts con nonce
    # válido → los navegadores modernos ignoran `unsafe-inline` cuando hay
    # nonce presente, cerrando el vector XSS clásico. Un atacante que
    # inyecte `<script>alert()</script>` no conoce el nonce del request
    # actual y su script queda bloqueado.
    @app.before_request
    def _generate_csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(18)

    @app.context_processor
    def _inject_csp_nonce():
        def _get_nonce():
            return getattr(g, "csp_nonce", "")
        return {"csp_nonce": _get_nonce}

    # ── Cabeceras de seguridad ──
    @app.after_request
    def set_security_headers(response):
        duration = round((time.time() - getattr(g, "start_time", time.time())) * 1000, 2)
        if not request.path.startswith("/static"):
            app.logger.info("%s %s -> %s (%sms)", request.method, request.path, response.status_code, duration)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        geolocation_allowed = request.path.startswith(("/repartidor/", "/checkout", "/api/check-address"))
        geolocation = "(self)" if geolocation_allowed else "()"
        response.headers["Permissions-Policy"] = (
            f"camera=(), geolocation={geolocation}, microphone=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Origin-Agent-Cluster"] = "?1"
        # CSP con nonce por request. `'strict-dynamic'` permite que un
        # script con nonce válido cargue otros dinámicamente. Sin
        # `'unsafe-inline'`: XSS clásico bloqueado. Los CDN se mantienen
        # explícitos por si algún template usa <script src="cdn/…">.
        nonce = getattr(g, "csp_nonce", "")
        response.headers["Content-Security-Policy"] = "; ".join((
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'self'",
            "form-action 'self'",
            f"script-src 'self' 'nonce-{nonce}' 'strict-dynamic' https: 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' data: https://fonts.gstatic.com",
            "img-src 'self' data: blob: https:",
            "connect-src 'self'",
            "manifest-src 'self'",
            "worker-src 'self' blob:",
        ))
        # HSTS: se emite cuando el request está en HTTPS (directamente o
        # detrás de proxy con X-Forwarded-Proto=https) O cuando la
        # configuración fuerza cookies seguras. Cubre el caso de nginx
        # que termina TLS y proxy_pass al Flask upstream sin TLS.
        forwarded_proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        is_https = request.is_secure or forwarded_proto == "https"
        if is_https and app.config.get("SESSION_COOKIE_SECURE"):
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
    """Falla pronto en producción si faltan variables críticas o son
    defaults débiles conocidos. Nunca corre en desarrollo — un fallo
    en boot es preferible a exponer una app con secretos comprometidos.
    """
    entorno = env or os.environ.get("FLASK_ENV", "development")
    if entorno != "production":
        return
    requeridas = ["SECRET_KEY", "DATABASE_URL", "BOT_API_KEY", "WEBHOOK_SECRET"]
    faltantes = [k for k in requeridas if not os.environ.get(k) and not app.config.get(k)]
    if faltantes:
        raise RuntimeError(f"Variables requeridas no configuradas: {faltantes}")
    if app.config.get("SECRET_KEY") in (None, "", "dev-key", "insecure"):
        raise RuntimeError("SECRET_KEY no puede ser el valor por defecto en producción")
    if len(str(app.config.get("SECRET_KEY") or "")) < 32:
        raise RuntimeError("SECRET_KEY debe tener ≥ 32 caracteres en producción")

    # Rechaza secrets con marcadores obvios de "no producción". Cierra el
    # error humano típico de dejar el .env.example en el server real.
    debiles = {
        "change-me", "changeme", "local-dev", "example", "insecure",
        "test", "dev", "default", "placeholder",
    }
    for var in ("BOT_API_KEY", "WEBHOOK_SECRET", "EVOLUTION_API_KEY", "BOT_PANEL_KEY"):
        val = str(os.environ.get(var) or app.config.get(var) or "").lower()
        if not val:
            continue
        if len(val) < 24:
            raise RuntimeError(f"{var} debe tener ≥ 24 caracteres en producción")
        if any(m in val for m in debiles):
            raise RuntimeError(f"{var} contiene marcador de default débil ({[m for m in debiles if m in val]})")

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
        ("NOMBRE_NEGOCIO",        _env_default("NOMBRE_NEGOCIO", "Mi tienda"), "Nombre del negocio"),
        ("SLOGAN_NEGOCIO",        _env_default("SLOGAN_NEGOCIO", ""), "Eslogan del negocio"),
        ("DESCRIPCION_NEGOCIO",   _env_default("DESCRIPCION_NEGOCIO", ""), "Descripción pública del negocio"),
        ("DIRECCION_NEGOCIO",     _env_default("DIRECCION_NEGOCIO", ""), "Direccion del local"),
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
        ("APP_ICON_URL",          _env_default("APP_ICON_URL", ""),          "Icono instalable de la PWA"),
        ("HERO_IMAGE_URL",        _env_default("HERO_IMAGE_URL", ""),        "Imagen principal del menú público"),
        ("TIENDA_URL",            _env_default("TIENDA_URL", ""),            "URL de la tienda web para pedidos (mostrado en WhatsApp)"),
        ("COLOR_PRIMARIO",        _env_default("COLOR_PRIMARIO", BRAND_COLOR_DEFAULTS["COLOR_PRIMARIO"]), "Color principal de marca"),
        ("COLOR_SECUNDARIO",      _env_default("COLOR_SECUNDARIO", BRAND_COLOR_DEFAULTS["COLOR_SECUNDARIO"]), "Color secundario de marca"),
        ("COLOR_ACENTO",          _env_default("COLOR_ACENTO", BRAND_COLOR_DEFAULTS["COLOR_ACENTO"]),   "Color de acento para estado y CTA"),
        ("HORARIO_APERTURA",      _env_default("HORARIO_APERTURA", "09:00"), "Hora de apertura tienda (HH:MM)"),
        ("HORARIO_CIERRE",        _env_default("HORARIO_CIERRE", "22:30"),   "Hora de cierre tienda (HH:MM)"),
        ("TIENDA_FORZAR_CERRADA", "0",                        "Forzar tienda cerrada (1/0). Prevalece sobre horario y FORZAR_ABIERTA."),
        ("TIENDA_FORZAR_ABIERTA", "0",                        "Forzar tienda abierta (1/0), ignorando horario. Útil para servicios fuera de franja horaria."),
        # Geo-validación de radio de entrega
        ("CIUDAD_NEGOCIO",                    _env_default("CIUDAD_NEGOCIO", ""),  "Ciudad del negocio (para geocodificación de direcciones)"),
        ("PROVINCIA_NEGOCIO",                 _env_default("PROVINCIA_NEGOCIO", ""), "Provincia del negocio (para geocodificación estructurada)"),
        ("PAIS_NEGOCIO",                      _env_default("PAIS_NEGOCIO", ""), "País del negocio"),
        ("PAIS_CODIGO_ISO",                   _env_default("PAIS_CODIGO_ISO", ""), "Código ISO del país"),
        ("EMAIL_CONTACTO",                    _env_default("EMAIL_CONTACTO", ""), "Correo público de contacto"),
        ("WHATSAPP_COUNTRY_CODE",             _env_default("WHATSAPP_COUNTRY_CODE", ""), "Prefijo telefónico internacional"),
        ("BIZUM_TELEFONO",                    _env_default("BIZUM_TELEFONO", ""), "Número que recibe Bizum"),
        ("BIZUM_HABILITADO",                  "1", "Permitir Bizum"),
        ("EFECTIVO_HABILITADO",               "1", "Permitir efectivo"),
        ("MODO_TIENDA",                       "propia", "Modo comercial: propia o bar_servicio"),
        ("FEATURE_DELIVERY",                  "1", "Permitir pedidos a domicilio"),
        ("FEATURE_RECOGIDA",                  "1", "Permitir pedidos para recoger"),
        ("FEATURE_PEDIDOS_PROGRAMADOS",       "1", "Permitir productos/pedidos con fecha de entrega"),
        ("FEATURE_PUNTOS",                    "1", "Activar club de puntos y canjes"),
        ("SERVICE_COMMISSION_PCT",            "0", "Porcentaje ganado por venta en modo servicio"),
        ("CENTRO_LAT",                        _env_default("CENTRO_LAT", ""),      "Latitud del centro de reparto"),
        ("CENTRO_LON",                        _env_default("CENTRO_LON", ""),      "Longitud del centro de reparto"),
        ("RADIO_ENTREGA_KM",                  _env_default("RADIO_ENTREGA_KM", "5"),      "Radio máximo de entrega en km desde el centro"),
        ("VALIDAR_RADIO_ENTREGA",             "1",          "Activar validación de radio de entrega (1/0)"),
        ("BLOQUEAR_DIRECCION_NO_VERIFICADA",  "1",          "Bloquear pedido si no se puede geocodificar la dirección (1/0)"),
    ]
    _defaults.extend(
        (key, value, "Token visual configurable de la tienda")
        for key, value in PUBLIC_THEME_DEFAULTS.items()
    )
    _defaults.extend(
        (key, value, "Texto público configurable de la tienda")
        for key, value in PUBLIC_UI_DEFAULTS.items()
    )
    for clave, valor, desc in _defaults:
        if not SiteConfig.query.filter_by(clave=clave).first():
            SiteConfig.set(clave, valor, descripcion=desc)
            changed = True

    # Sembrado de claves nuevas (fiscal + anti-hardcoding). Idempotente:
    # solo escribe las que aún no existen.
    try:
        from config_defaults import sembrar_defaults
        if sembrar_defaults() > 0:
            changed = True
    except Exception:
        # No queremos que un fallo aquí impida arrancar la app; el resto
        # de defaults sí se persistieron.
        pass

    if changed:
        db.session.commit()


def _seed_operational_basics():
    """Crea solo datos necesarios para operar, sin clientes ni pedidos demo."""
    from models import AdminFeature, ADMIN_FEATURES, SiteConfig, User, ZonaEntrega, utcnow

    changed = False
    seed_pw = _seed_password()
    now = utcnow()
    seed_staff_default = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "production")) != "production"
    seed_staff = _to_bool(os.environ.get("OXIDIAN_SEED_STAFF"), seed_staff_default)
    minimal_users = _to_bool(os.environ.get("OXIDIAN_MINIMAL_USERS"), False)

    # El seed automático crea la dotación mínima de preparación y reparto.
    # "cocina" sigue siendo un rol vigente para pedidos inmediatos, pero sus
    # cuentas se crean desde administración según la operación de cada tienda.
    _domain = os.environ.get("STAFF_EMAIL_DOMAIN", "oxidian.local").strip() or "oxidian.local"
    staff_users = []
    if seed_staff:
        if minimal_users:
            staff_users = [
                {"nombre": "Preparación", "email": f"preparacion@{_domain}", "rol": "preparacion", "puesto_trabajo": "Armado de pedidos"},
                {"nombre": "Repartidor", "email": f"repartidor@{_domain}", "rol": "repartidor", "puesto_trabajo": "Reparto", "tarifa_entrega": 2.5},
            ]
        else:
            staff_users = [
                {"nombre": "Preparación 1", "email": f"prep1@{_domain}", "rol": "preparacion", "puesto_trabajo": "Cocina / Almacén"},
                {"nombre": "Preparación 2", "email": f"prep2@{_domain}", "rol": "preparacion", "puesto_trabajo": "Encargos"},
                {"nombre": "Repartidor",    "email": f"repartidor@{_domain}", "rol": "repartidor",  "puesto_trabajo": "Reparto", "tarifa_entrega": 2.5},
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
            nombre="Zona principal",
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
    # El catálogo demo usa una dotación reducida; las cuentas de cocina se
    # crean desde administración cuando el flujo de pedidos inmediatos lo exige.
    # Dominio de email neutralizado para no filtrar branding del vendor.
    _demo_domain = os.environ.get("STAFF_EMAIL_DOMAIN", "oxidian.local").strip() or "oxidian.local"
    demo_users = [
        {"nombre": "Preparación",    "email": f"preparacion@{_demo_domain}", "rol": "preparacion", "puesto_trabajo": "Cocina / Almacén"},
        {"nombre": "Encargos",       "email": f"encargos@{_demo_domain}",    "rol": "preparacion", "puesto_trabajo": "Gestión encargos"},
        {"nombre": "Repartidor",     "email": f"repartidor@{_demo_domain}",  "rol": "repartidor",  "tarifa_entrega": 2.5},
        {"nombre": "Cliente Demo 1", "email": f"cliente1@{_demo_domain}",    "rol": "cliente",     "telefono": "600000001", "puntos": 250},
        {"nombre": "Cliente Demo 2", "email": f"cliente2@{_demo_domain}",    "rol": "cliente",     "telefono": "600000002", "puntos": 80},
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
        ("Fritos & Snacks", "Tequeños, buñuelos y aperitivos crujientes", 2,  "https://images.unsplash.com/photo-1601050690597-df0568f70950?w=200&fit=crop"),
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
         "El favorito del barrio: snacks a elegir + 1 bebida a elegir. ¡Todo por menos!",
         6.50, 2.80, "Combos", 0, "inmediato",
         {"imagen_url": U.format("1550547660-d9450f859349"),
          "origen_pais": "Local",
          "es_combo": True}),

        ("Combo Familiar",
         "Para toda la familia: snacks surtidos + 2 bebidas a elegir + 2 postres.",
         18.50, 8.50, "Combos", 0, "inmediato",
         {"imagen_url": U.format("1504674900247-0877df9cc836"),
          "origen_pais": "Local",
          "es_combo": True}),

        ("Combo Arepa Feliz",
         "Arepa Reina Pepiada + Malta o Jugo Natural. El almuerzo perfecto. Producto programado.",
         7.50, 3.50, "Combos", 0, "programado",
         {"imagen_url": U.format("1546069901-ba9599a7e63c"),
          "origen_pais": "Local",
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

    # Combo Parcerito: 2x snack + 1 bebida (seleccionable)
    combo1 = pid("Combo Parcerito")
    malta   = pid("Malta Colombiana 33cl")
    agua    = pid("Agua Mineral 33cl")
    jugo    = pid("Jugo Natural de Lulo")
    if combo1 and not ComboItem.query.filter_by(combo_id=combo1.id).first():
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

    # Combo Familiar: snacks + 2 bebidas (seleccionable) + 2 postres fijos
    combo2 = pid("Combo Familiar")
    natilla = pid("Natilla Colombiana")
    majarete = pid("Majarete de Coco")
    teq = pid("Tequeños de Queso (4 uds)")
    if combo2 and not ComboItem.query.filter_by(combo_id=combo2.id).first():
        for ep in [teq]:
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

    # ── Zonas de entrega (genéricas, sin hardcodear ciudad) ───────
    # El nombre real de las zonas lo configura el admin en /superadmin/zonas.
    # Aquí sembramos placeholders neutros solo si no hay ninguna zona aún.
    if not ZonaEntrega.query.first():
        db.session.add(ZonaEntrega(
            nombre="Centro", descripcion="Casco urbano principal",
            es_epicentro=True, activo=True,
            precio_envio=0, tiempo_estimado_min=20, gratis_desde=20, orden=1,
        ))
        db.session.add(ZonaEntrega(
            nombre="Extrarradio", descripcion="Zonas periféricas",
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

        cliente1 = (User.query.filter_by(email=f"cliente1@{_demo_domain}").first()
                    or User.query.filter_by(email="cliente@oxidian.com").first())
        cliente2 = (User.query.filter_by(email=f"cliente2@{_demo_domain}").first()
                    or User.query.filter_by(email="cliente2@oxidian.com").first())
        zona = (ZonaEntrega.query.filter_by(es_epicentro=True).first()
                or ZonaEntrega.query.first())

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
                direccion_entrega="Dirección demo 1",
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
                        [(teq, 2), (malta, 1)], zona)
            _make_order("OX-DEMO-002", cliente2, "entregado",  "bizum",    5,
                        [(teq, 1), (bunuelos, 1), (agua, 2)], zona)
            _make_order("OX-DEMO-003", cliente1, "entregado",  "efectivo", 4,
                        [(combo1, 1), (natilla, 1)], zona)
            _make_order("OX-DEMO-004", cliente2, "entregado",  "bizum",    3,
                        [(bunuelos, 3), (jugo, 1), (majarete, 1)], zona)
            _make_order("OX-DEMO-005", cliente1, "entregado",  "efectivo", 2,
                        [(teq, 1), (bunuelos, 1), (agua, 1)], zona)
            _make_order("OX-DEMO-006", cliente2, "en_ruta",    "bizum",    0,
                        [(combo1, 1), (agua, 1)], zona)
            _make_order("OX-DEMO-007", cliente1, "armando",    "efectivo", 0,
                        [(teq, 2), (malta, 1)], zona)
            _make_order("OX-DEMO-008", cliente2, "pendiente",  "bizum",    0,
                        [(bunuelos, 1), (jugo, 2)], zona)
            _make_order("OX-DEMO-009", cliente1, "cancelado",  "efectivo", 1,
                        [(bunuelos, 2), (agua, 1)], zona)
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
