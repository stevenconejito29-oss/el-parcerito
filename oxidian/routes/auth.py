import os
import time

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app
from urllib.parse import urlparse
from flask_login import login_user, logout_user, login_required, current_user
from extensions import limiter, db
from models import ROLES_AUTENTICABLES, User

auth_bp = Blueprint("auth", __name__)

REDIRECT_POR_ROL = {
    "super_admin":  "superadmin.dashboard",
    "admin":        "admin.dashboard",
    "preparacion":  "preparador.pedidos",
    "repartidor":   "repartidor.ruta",
    "cocina":       "preparador.pedidos",
}

# Tras un password válido, el usuario tiene N segundos para introducir el TOTP.
# Pasado ese tiempo, se limpia la intención y hay que volver a autenticarse.
# 5 minutos es holgado para copiar el código pero corto ante robo de sesión.
MFA_PENDING_TTL_SECONDS = 300

# Roles a los que SIEMPRE se les obliga a tener MFA activo. El primer GET
# a cualquier ruta protegida tras un login válido los manda al setup si
# aún no han activado MFA.
#
# Antes: solo super_admin llevaba MFA. Un admin phished sin 2º factor =
# tienda comprometida (acceso a /admin/usuarios, cambio de contraseñas,
# manipulación de pedidos). Ahora admin también entra en el set —
# configurable vía env `MFA_ROLES_OBLIGATORIOS` para negocios que
# quieran ampliar (ej. cocina, repartidor) sin tocar código.
def _roles_mfa_obligatorio():
    raw = (os.environ.get("MFA_ROLES_OBLIGATORIOS") or "").strip()
    if raw:
        return {r.strip() for r in raw.split(",") if r.strip()}
    return {"super_admin", "admin"}

ROLES_MFA_OBLIGATORIO = _roles_mfa_obligatorio()


# ── LOGIN ───────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute") if limiter else (lambda f: f)
def login():
    if current_user.is_authenticated:
        return _redirect_rol(current_user.rol)

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter(
            User.email == email,
            User.activo == True,
            User.rol.in_(ROLES_AUTENTICABLES),
        ).first()

        # Ejecuta la verificación de password aun si el usuario no existe para
        # que la latencia sea indistinguible entre "email no registrado" y
        # "password incorrecto". Previene enumeración de emails por timing.
        if user and user.puede_iniciar_sesion:
            password_ok = user.check_password(password)
        else:
            _check_password_dummy(password)
            password_ok = False

        if user and user.puede_iniciar_sesion and password_ok:
            # Si el user tiene MFA activo: NO completar login todavía. Guardamos
            # un "intent" en la sesión con timestamp y le pedimos el código TOTP.
            if user.mfa_enabled and user.mfa_secret:
                session["mfa_pending_user_id"] = user.id
                session["mfa_pending_next"] = request.args.get("next") or ""
                session["mfa_pending_at"] = int(time.time())
                # Snapshot del hash para invalidar la intención si un admin
                # cambia la contraseña mientras el usuario está en MFA challenge.
                session["mfa_pending_pw_hash"] = user.password_hash or ""
                return redirect(url_for("auth.mfa_challenge"))

            _complete_login(user)
            next_page = request.args.get("next")
            if _next_is_safe_get(next_page):
                return redirect(next_page)
            return _redirect_rol(user.rol)

        flash("Email o contraseña incorrectos.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/login/mfa", methods=["GET", "POST"])
@limiter.limit("10 per minute") if limiter else (lambda f: f)
def mfa_challenge():
    """Segundo factor: pide el código TOTP tras un password válido."""
    pending_id = session.get("mfa_pending_user_id")
    if not pending_id:
        return redirect(url_for("auth.login"))
    # TTL: si el usuario tardó > MFA_PENDING_TTL_SECONDS, abortar y forzar login.
    started_at = int(session.get("mfa_pending_at") or 0)
    if started_at and (int(time.time()) - started_at) > MFA_PENDING_TTL_SECONDS:
        _clear_mfa_pending()
        flash("El tiempo para introducir el código de verificación expiró. Inicia sesión de nuevo.", "warning")
        return redirect(url_for("auth.login"))
    user = db.session.get(User, pending_id)
    if not user or not user.puede_iniciar_sesion or not user.mfa_enabled or not user.mfa_secret:
        _clear_mfa_pending()
        return redirect(url_for("auth.login"))
    # Invalidar si la contraseña cambió entre login y MFA challenge.
    # Comparación constant-time del hash COMPLETO (evita colisión accidental
    # sobre un prefijo corto y timing side channel en la comparación).
    import hmac
    expected_hash = session.get("mfa_pending_pw_hash") or ""
    current_hash = user.password_hash or ""
    if expected_hash and not hmac.compare_digest(expected_hash, current_hash):
        _clear_mfa_pending()
        flash("Tu contraseña cambió. Inicia sesión de nuevo.", "warning")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        code = (request.form.get("code") or "").strip().replace(" ", "")
        if _verify_totp(user.mfa_secret, code):
            next_page = session.pop("mfa_pending_next", "") or None
            _clear_mfa_pending()
            _complete_login(user)
            if _next_is_safe_get(next_page):
                return redirect(next_page)
            return _redirect_rol(user.rol)
        flash("Código incorrecto. Intenta de nuevo.", "danger")

    return render_template("auth/mfa_challenge.html")


def _clear_mfa_pending():
    """Limpia todas las llaves de sesión relacionadas con MFA pending."""
    for key in ("mfa_pending_user_id", "mfa_pending_next",
                "mfa_pending_at", "mfa_pending_pw_hash"):
        session.pop(key, None)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    current_user.en_linea = False
    db.session.commit()
    logout_user()
    session.pop("mfa_v", None)
    _clear_mfa_pending()
    return redirect(url_for("public.index"))


# ── MFA SETUP / DISABLE ─────────────────────────────────────────────────────

@auth_bp.route("/perfil/mfa", methods=["GET", "POST"])
@login_required
def mfa_setup():
    """Genera un secreto TOTP, muestra el QR y exige verificación para activar."""
    import pyotp

    if request.method == "GET" and current_user.mfa_enabled:
        return render_template("auth/mfa_setup.html", already_enabled=True)

    if request.method == "GET":
        secret = pyotp.random_base32()
        session["mfa_setup_secret"] = secret
        from store_config import get_store_value
        otpauth_url = pyotp.totp.TOTP(secret).provisioning_uri(
            name=current_user.email,
            issuer_name=get_store_value("NOMBRE_NEGOCIO"),
        )
        qr_svg = _qr_svg_inline(otpauth_url)
        return render_template(
            "auth/mfa_setup.html",
            already_enabled=False,
            secret=secret,
            qr_svg=qr_svg,
            otpauth_url=otpauth_url,
        )

    # POST: verificar código y activar
    secret = session.get("mfa_setup_secret")
    code = (request.form.get("code") or "").strip().replace(" ", "")
    if not secret:
        flash("La sesión de configuración expiró. Vuelve a empezar.", "warning")
        return redirect(url_for("auth.mfa_setup"))
    if not _verify_totp(secret, code):
        flash("Código incorrecto. Inténtalo de nuevo.", "danger")
        return redirect(url_for("auth.mfa_setup"))

    current_user.mfa_secret = secret
    current_user.mfa_enabled = True
    current_user.mfa_session_version = (current_user.mfa_session_version or 0) + 1
    db.session.commit()
    session.pop("mfa_setup_secret", None)
    session["mfa_v"] = current_user.mfa_session_version
    flash("Verificación en dos pasos activada.", "success")
    return _redirect_rol(current_user.rol)


@auth_bp.route("/perfil/mfa/disable", methods=["POST"])
@login_required
def mfa_disable():
    """Desactivar MFA exige el password actual + un código TOTP válido."""
    password = request.form.get("password") or ""
    code = (request.form.get("code") or "").strip().replace(" ", "")
    if not current_user.check_password(password):
        flash("Contraseña incorrecta.", "danger")
        return redirect(url_for("auth.mfa_setup"))
    if not current_user.mfa_enabled or not current_user.mfa_secret:
        flash("MFA no estaba activo.", "info")
        return redirect(url_for("auth.mfa_setup"))
    if not _verify_totp(current_user.mfa_secret, code):
        flash("Código incorrecto.", "danger")
        return redirect(url_for("auth.mfa_setup"))

    current_user.mfa_enabled = False
    current_user.mfa_secret = None
    current_user.mfa_session_version = (current_user.mfa_session_version or 0) + 1
    db.session.commit()
    session["mfa_v"] = current_user.mfa_session_version
    flash("Verificación en dos pasos desactivada.", "warning")
    return redirect(url_for("auth.mfa_setup"))


# ── Helpers ─────────────────────────────────────────────────────────────────

def _complete_login(user):
    if not user.puede_iniciar_sesion:
        raise ValueError("El usuario no pertenece a un rol interno autenticable.")
    session.permanent = True
    login_user(user, remember=False)
    session["mfa_v"] = int(user.mfa_session_version or 0)
    # Notificación de detección de intrusiones: cuando un admin/super_admin
    # inicia sesión, avisamos por push web a los DEMÁS admins/super_admins.
    # Si tu cuenta es comprometida vía SIM-swap o phishing, los otros
    # admins ven la alerta y pueden pulsar el modo pánico o cambiar
    # contraseñas antes de que se haga daño. La notificación incluye IP y
    # user-agent para saber si el login coincide con tu dispositivo real.
    if user.rol in ("admin", "super_admin"):
        try:
            _notificar_login_admin(user)
        except Exception:
            # No bloqueamos el login si el push falla — sería un DoS
            # accidental. Solo se registra el fallo para diagnóstico.
            from flask import current_app
            current_app.logger.exception("push notify_admin_login")


def _notificar_login_admin(user):
    """Envía push a los OTROS admins avisando de un login de admin nuevo.

    El usuario que acaba de entrar NO recibe la alerta (redundante), pero
    todos los demás sí. Ideal para detectar SIM-swap / phishing rápido.
    """
    from models import PushSubscription, User as _U
    from push_service import _build_payload, _dispatch
    # IP y user-agent para que el receptor pueda validar si es un
    # dispositivo esperado o algo raro.
    ip = (request.headers.get("X-Real-IP") or request.remote_addr or "?").strip()
    ua_raw = (request.headers.get("User-Agent") or "?").strip()
    # Recortamos el UA para que quepa en la notificación sin cortar palabras.
    ua = (ua_raw[:80] + "…") if len(ua_raw) > 80 else ua_raw
    subs = PushSubscription.query.filter(
        PushSubscription.activo.is_(True),
        PushSubscription.usuario.has(_U.rol.in_(("admin", "super_admin"))),
        PushSubscription.user_id != user.id,   # no notificar al propio user
    ).all()
    if not subs:
        return
    titulo = f"🔐 Login admin: {user.nombre}"
    cuerpo = f"Desde IP {ip}. Si NO fuiste tú/tu equipo, escribe *!emergency_on* al bot."
    payload = _build_payload(
        titulo, cuerpo,
        url="/admin/dashboard",
        tag=f"admin-login-{user.id}",   # colapsa alertas repetidas del mismo user
        require_interaction=False,
    )
    _dispatch(subs, payload)


def _verify_totp(secret, code):
    import pyotp
    if not secret or not code or not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# Hash scrypt pregenerado (cost equivalente al de producción) sobre una
# contraseña que no se corresponde con ningún usuario. Se usa para nivelar
# la latencia del login cuando el email no existe.
_DUMMY_HASH_CACHE = None


def _check_password_dummy(password):
    """Ejecuta un check_password contra un hash dummy para uniformar latencia.

    Cuando el email tecleado no coincide con ningún usuario, hay que gastar
    aproximadamente el mismo CPU que gastaría un check real; de lo contrario
    un atacante puede enumerar usuarios midiendo el tiempo de respuesta.
    """
    from werkzeug.security import check_password_hash, generate_password_hash
    global _DUMMY_HASH_CACHE
    if _DUMMY_HASH_CACHE is None:
        _DUMMY_HASH_CACHE = generate_password_hash("timing-shield-placeholder")
    check_password_hash(_DUMMY_HASH_CACHE, password or "")


def _qr_svg_inline(data):
    """Devuelve un SVG inline del QR sin dependencias de Pillow."""
    import io
    import segno
    qr = segno.make(data, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=6, border=2, xmldecl=False, svgns=False)
    return buf.getvalue().decode("utf-8")


def _redirect_rol(rol):
    destino = REDIRECT_POR_ROL.get(rol)
    if not destino:
        # Rol legacy o corrupto (p.ej. "staff" antiguo). Fuerza logout limpio
        # y da feedback al usuario en lugar de un loop silencioso hacia login.
        current_app.logger.warning("_redirect_rol: rol sin destino: %r", rol)
        logout_user()
        session.pop("mfa_v", None)
        _clear_mfa_pending()
        flash(
            "Tu rol no tiene un panel asignado. Contacta con administración.",
            "warning",
        )
        return redirect(url_for("auth.login"))
    return redirect(url_for(destino))


def _next_is_safe_get(next_page):
    if not next_page:
        return False
    parsed = urlparse(next_page)
    if parsed.scheme or parsed.netloc:
        return False
    path = parsed.path or next_page
    unsafe_action_fragments = (
        "/toggle", "/eliminar", "/crear", "/editar", "/pagar",
        "/confirmar", "/rechazar", "/cancelar", "/devolver",
        "/agregar", "/ajustar", "/activar-todos", "/guardar",
    )
    return not any(fragment in path for fragment in unsafe_action_fragments)
