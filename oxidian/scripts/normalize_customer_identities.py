#!/usr/bin/env python3
"""Normaliza identidades de clientes públicos.

Los clientes no son cuentas autenticables; se identifican por teléfono para
pedidos, puntos y reseñas. Este script reemplaza emails visibles antiguos por
emails técnicos internos sin tocar pedidos ni saldos.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from extensions import db
from models import CUSTOMER_INTERNAL_EMAIL_DOMAIN, User, internal_customer_email


def _target_email(user: User) -> str:
    base = internal_customer_email(user.telefono_normalizado or user.telefono or f"id{user.id}")
    if not User.query.filter(User.id != user.id, User.email == base).first():
        return base
    return internal_customer_email(user.telefono_normalizado or user.telefono or f"id{user.id}", uuid.uuid4().hex[:6])


def main() -> None:
    app = create_app(os.environ.get("FLASK_ENV", "production"))
    with app.app_context():
        changed = 0
        for user in User.query.filter_by(rol="cliente").all():
            if (user.email or "").endswith(f"@{CUSTOMER_INTERNAL_EMAIL_DOMAIN}"):
                continue
            user.email = _target_email(user)
            changed += 1
        db.session.commit()
        print({"clientes_normalizados": changed})


if __name__ == "__main__":
    main()
