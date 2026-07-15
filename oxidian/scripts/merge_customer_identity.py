"""Consolida un cliente legacy dentro de su cuenta canónica.

Por seguridad funciona en dry-run salvo que se indique ``--apply``. Conserva
el usuario fuente inactivo para no romper referencias históricas de auditoría.
"""
import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("OXIDIAN_SKIP_STARTUP_DB", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from extensions import db
from models import AuditLog, User


CUSTOMER_REFERENCES = (
    ("orders", "cliente_id"),
    ("points_log", "cliente_id"),
    ("reviews", "cliente_id"),
    ("affiliate_uses", "cliente_id"),
    ("push_subscriptions", "user_id"),
    ("notification_outbox", "user_id"),
)


def _counts(source_id):
    inspector = db.inspect(db.engine)
    tables = set(inspector.get_table_names())
    result = {}
    for table, column in CUSTOMER_REFERENCES:
        if table not in tables:
            continue
        result[f"{table}.{column}"] = db.session.execute(
            db.text(f"SELECT COUNT(*) FROM {table} WHERE {column}=:uid"),
            {"uid": source_id},
        ).scalar_one()
    return result


def merge(source_id, target_id, apply=False):
    source = db.session.execute(
        db.select(User).where(User.id == source_id).with_for_update()
    ).scalar_one_or_none()
    target = db.session.execute(
        db.select(User).where(User.id == target_id).with_for_update()
    ).scalar_one_or_none()
    if not source or not target or source.id == target.id:
        raise ValueError("source/target inválidos")
    if source.rol != "cliente":
        raise ValueError("el perfil fuente debe ser cliente")

    before = _counts(source.id)
    report = {
        "source": {"id": source.id, "rol": source.rol, "telefono": source.telefono_normalizado},
        "target": {"id": target.id, "rol": target.rol, "telefono": target.telefono_normalizado},
        "puntos_source": int(source.puntos or 0),
        "puntos_target": int(target.puntos or 0),
        "references": before,
        "apply": bool(apply),
    }
    if not apply:
        db.session.rollback()
        return report

    for table, column in CUSTOMER_REFERENCES:
        if f"{table}.{column}" not in before:
            continue
        db.session.execute(
            db.text(f"UPDATE {table} SET {column}=:target WHERE {column}=:source"),
            {"target": target.id, "source": source.id},
        )

    target.puntos = int(target.puntos or 0) + int(source.puntos or 0)
    if not target.direccion and source.direccion:
        target.direccion = source.direccion
    if not target.nif and source.nif:
        target.nif = source.nif
    source.puntos = 0
    source.activo = False
    source.telefono = None
    source.telefono_normalizado = None
    source.cod_puntos = None
    source.cod_puntos_expira = None
    source.cod_puntos_intentos = 0
    AuditLog.registrar(
        target.id,
        "merge_customer_identity",
        "user",
        source.id,
        detalle=f"Referencias consolidadas: {before}",
        ip="maintenance",
    )
    db.session.commit()
    report["puntos_resultado"] = int(target.puntos or 0)
    report["remaining"] = _counts(source.id)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=int, required=True)
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    app = create_app(os.environ.get("FLASK_ENV", "production"))
    with app.app_context():
        print(merge(args.source, args.target, args.apply))


if __name__ == "__main__":
    main()
