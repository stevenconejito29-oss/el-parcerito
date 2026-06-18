"""Prueba de humo del flujo de puntos identificado únicamente por teléfono."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.exc import IntegrityError

from app import create_app
from extensions import db
from models import SiteConfig, User


TEST_EMAIL = "qa-puntos-temporal@oxidian.local"
TEST_PHONE = "+34699000123"


def _csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise AssertionError("No se encontró el token CSRF")
    return match.group(1)


def main() -> None:
    app = create_app("production")
    app.config.update(TESTING=True)

    with app.app_context():
        User.query.filter_by(email=TEST_EMAIL).delete()
        db.session.commit()
        cliente = User(
            nombre="QA puntos temporal",
            email=TEST_EMAIL,
            rol="cliente",
            telefono="699 000 123",
            puntos=275,
        )
        cliente.set_password("unused-smoke-password")
        db.session.add(cliente)
        db.session.commit()
        cliente_id = cliente.id

    try:
        client = app.test_client()
        club = client.get("/club")
        assert club.status_code == 200
        csrf = _csrf_from(club.get_data(as_text=True))
        headers = {"X-CSRFToken": csrf}

        public_lookup = client.get("/api/public/cliente?telefono=699000123")
        lookup_data = public_lookup.get_json()
        assert public_lookup.status_code == 200
        assert lookup_data == {"ok": True, "telefono": TEST_PHONE}
        assert not {"existe", "nombre", "direccion", "puntos"} & set(lookup_data)

        with patch("routes.public.enviar_saldo_puntos", return_value=True):
            balance = client.post(
                "/puntos/consultar-saldo",
                json={"telefono": "699 000 123"},
                headers=headers,
            )
        assert balance.status_code == 200
        assert "275" not in balance.get_data(as_text=True)

        with patch("services.enviar_whatsapp_generico", return_value=True):
            otp_response = client.post(
                "/puntos/solicitar-codigo",
                json={"telefono": "0034 699 000 123"},
                headers=headers,
            )
        assert otp_response.status_code == 200
        otp_payload = otp_response.get_json()
        assert otp_payload["ok"] is True
        assert "puntos" not in otp_payload

        with app.app_context():
            cliente = db.session.get(User, cliente_id)
            codigo = cliente.cod_puntos
            assert codigo and len(codigo) == 6

        verification = client.post(
            "/puntos/verificar-codigo",
            json={
                "telefono": "+34 699 000 123",
                "codigo": codigo,
                "puntos": 0,
                "producto_canje_id": None,
            },
            headers=headers,
        )
        verification_data = verification.get_json()
        assert verification.status_code == 200
        assert verification_data["ok"] is True
        assert verification_data["puntos_totales"] == 275

        duplicate_rejected = False
        with app.app_context():
            duplicate = User(
                nombre="QA duplicado",
                email="qa-puntos-duplicado@oxidian.local",
                rol="cliente",
                telefono="699000123",
            )
            duplicate.set_password("unused-smoke-password")
            db.session.add(duplicate)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                duplicate_rejected = True
        assert duplicate_rejected, "La base de datos permitió un teléfono duplicado"

        from services import validar_radio_entrega

        with app.app_context():
            centro_lat = float(SiteConfig.get("CENTRO_LAT", "37.4698"))
            centro_lon = float(SiteConfig.get("CENTRO_LON", "-5.6435"))
            with patch(
                "services.geocodificar_direccion",
                return_value=(centro_lat, centro_lon),
            ):
                inside = validar_radio_entrega("Calle Real 5")
            assert inside["ok"] is True and inside["distancia_km"] == 0.0

            with patch(
                "services.geocodificar_direccion",
                return_value=(centro_lat + 1.0, centro_lon),
            ):
                outside = validar_radio_entrega("Calle Fuera 99")
            assert outside["ok"] is False and outside["distancia_km"] > 50

        print({
            "ok": True,
            "telefono_canonico": TEST_PHONE,
            "saldo_privado": True,
            "otp_verificado": True,
            "duplicado_rechazado": True,
            "radio_entrega": True,
        })
    finally:
        with app.app_context():
            User.query.filter(
                User.email.in_([TEST_EMAIL, "qa-puntos-duplicado@oxidian.local"])
            ).delete(synchronize_session=False)
            db.session.commit()


if __name__ == "__main__":
    main()
