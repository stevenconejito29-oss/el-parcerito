"""Expira Cruces 'open' sin ofertas tras CRUCE_TIMEOUT_MIN minutos.

Idempotente. Pensado para cron (cada 5-10 minutos) como respaldo a la
expiración perezosa que hace el propio endpoint público al listar/pollear.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from cruce_policy import expire_stale_open_favors


def main() -> int:
    app = create_app()
    with app.app_context():
        expired = expire_stale_open_favors()
        if not expired:
            print("[expire_stale_favors] Sin Cruces expirados.")
            return 0
        try:
            from push_service import notify_user
            for row in expired:
                if row.customer_id:
                    notify_user(
                        row.customer_id, "⌛ Tu Cruce expiró sin ofertas",
                        "Puedes publicarlo de nuevo ajustando el precio o el detalle.",
                        url="/favor", tag=f"cruce-expired-{row.id}",
                    )
        except Exception:
            app.logger.exception("No se pudieron notificar Cruces expirados")
        print(f"[expire_stale_favors] Expirados: {len(expired)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
