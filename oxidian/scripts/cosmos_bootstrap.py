#!/usr/bin/env python3
"""Bootstrap idempotente para la pila Cosmos/local.

La app crea tablas y usuarios base al importar create_app(). Este script solo
sincroniza configuración que debe venir de variables de entorno y, si se pide,
aplica el catálogo showcase para tener datos completos al probar.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from extensions import db


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def main() -> None:
    app = create_app(env("APP_ENV", "production"))
    with app.app_context():
        from models import SiteConfig

        mappings = {
            "BOT_API_URL": env("BOT_API_URL"),
            "BOT_PUBLIC_URL": env("BOT_PUBLIC_URL"),
            "BOT_API_KEY": env("BOT_API_KEY"),
            "BOT_PANEL_KEY": env("BOT_PANEL_KEY", env("BOT_API_KEY")),
            "OXIDIAN_PUBLIC_URL": env("OXIDIAN_PUBLIC_URL"),
            "TIENDA_URL": env("TIENDA_URL", env("OXIDIAN_PUBLIC_URL")),
            "NOMBRE_NEGOCIO": env("NOMBRE_NEGOCIO"),
            "TELEFONO_NEGOCIO": env("TELEFONO_NEGOCIO", env("OWNER_NUMBER")),
            "DIRECCION_NEGOCIO": env("DIRECCION_NEGOCIO"),
            "EVOLUTION_API_URL": env("EVOLUTION_API_URL"),
            "EVOLUTION_API_KEY": env("EVOLUTION_API_KEY"),
            "EVOLUTION_INSTANCE": env("EVOLUTION_INSTANCE"),
            "WEBHOOK_SECRET": env("WEBHOOK_SECRET"),
        }
        # Claves de bootstrap "fuerte" (deben venir siempre del env; suelen ser
        # secretos/URLs de infraestructura). Se sobreescriben en cada arranque.
        BOOTSTRAP_FUERTE = {
            "BOT_API_URL", "BOT_PUBLIC_URL", "BOT_API_KEY", "BOT_PANEL_KEY",
            "OXIDIAN_PUBLIC_URL",
            "EVOLUTION_API_URL", "EVOLUTION_API_KEY", "EVOLUTION_INSTANCE",
            "WEBHOOK_SECRET",
        }
        # El resto son valores EDITABLES desde /superadmin/config. El env solo
        # se usa como bootstrap inicial — si ya hay valor en BD, NO se pisa.
        # Fix de 2026-07-02: antes reseteaba NOMBRE_NEGOCIO, TELEFONO_NEGOCIO,
        # etc. en cada restart, deshaciendo los cambios del admin.
        for key, value in mappings.items():
            if not value:
                continue
            if key in BOOTSTRAP_FUERTE:
                SiteConfig.set(key, value)
            else:
                existente = SiteConfig.get(key, "")
                if not str(existente or "").strip():
                    SiteConfig.set(key, value)

        db.session.commit()

        if env("SEED_SHOWCASE_CATALOG", "0").lower() in ("1", "true", "yes", "si", "sí"):
            from scripts.seed_showcase_catalog import replace_catalog

            result = replace_catalog()
            print(
                "Showcase catalog listo: "
                f"{result['categories']} categorias, "
                f"{result['products']} productos, "
                f"{result['combos']} combos."
            )

        print("Cosmos bootstrap OK")


if __name__ == "__main__":
    main()
