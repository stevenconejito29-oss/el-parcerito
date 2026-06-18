from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from urllib.parse import urlparse
from flask_login import login_user, logout_user, login_required, current_user
from extensions import limiter, db
from models import User

auth_bp = Blueprint("auth", __name__)

REDIRECT_POR_ROL = {
    "super_admin":  "superadmin.dashboard",
    "admin":        "admin.dashboard",
    "preparacion":  "preparador.pedidos",
    "repartidor":   "repartidor.ruta",
    "proveedor":    "proveedor.pedidos",
    "cliente":      "public.index",
    # Aliases retro-compatibles para usuarios con valores antiguos en BD.
    "cocina":       "preparador.pedidos",
    "staff":        "preparador.pedidos",
}

# Roles a los que SIEMPRE se les obliga a tener MFA activo. El primer GET a
# cualquier ruta protegida tras un login válido los manda al setup si aún no
# han activado MFA.
ROLES_MFA_OBLIGATORIO = {"super_admin"}


# ── LOGIN ───────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute") if limiter else (lambda f: f)
def login():
    if current_user.is_authenticated:
        return _redirect_rol(current_user.rol)

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email, activo=True).first()

        if user and user.check_password(password):
            # Si el user tiene MFA activo: NO completar login todavía. Guardamos
            # un "intent" en la sesión y le pedimos el código TOTP.
            if user.mfa_enabled and user.mfa_secret:
                session["mfa_pending_user_id"] = user.id
                session["mfa_pending_next"] = request.args.get("next") or ""
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
    user = db.session.get(User, pending_id)
    if not user or not user.activo or not user.mfa_enabled or not user.mfa_secret:
        session.pop("mfa_pending_user_id", None)
        session.pop("mfa_pending_next", None)
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        code = (request.form.get("code") or "").strip().replace(" ", "")
        if _verify_totp(user.mfa_secret, code):
            next_page = session.pop("mfa_pending_next", "") or None
            session.pop("mfa_pending_user_id", None)
            _complete_login(user)
            if _next_is_safe_get(next_page):
                return redirect(next_page)
            return _redirect_rol(user.rol)
        flash("Código incorrecto. Intenta de nuevo.", "danger")

    return render_template("auth/mfa_challenge.html")


@auth_bp.route("/registro")
def registro():
    from flask import abort as _abort
    _abort(404)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    current_user.en_linea = False
    db.session.commit()
    logout_user()
    session.pop("mfa_v", None)
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
        otpauth_url = pyotp.totp.TOTP(secret).provisioning_uri(
            name=current_user.email,
            issuer_name="Oxidian — El Parcerito",
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
    session.permanent = True
    login_user(user, remember=False)
    session["mfa_v"] = int(user.mfa_session_version or 0)


def _verify_totp(secret, code):
    import pyotp
    if not secret or not code or not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def _qr_svg_inline(data):
    """Devuelve un SVG inline del QR sin dependencias de Pillow."""
    import io
    import segno
    qr = segno.make(data, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=6, border=2, xmldecl=False, svgns=False)
    return buf.getvalue().decode("utf-8")


def _redirect_rol(rol):
    destino = REDIRECT_POR_ROL.get(rol, "public.index")
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
