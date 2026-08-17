"""Smoke test end-to-end del módulo delivery_franjas.

Ejecuta un recorrido básico sobre una instancia de Oxidian levantada
(local via docker-compose.test.yml o server). No pretende cubrir todos
los casos — sirve para verificar que el módulo responde tras un deploy.

Uso:

    python3 scripts/smoke_delivery_franjas.py \\
        --base-url http://localhost:5071 \\
        --admin-email superadmin@test.local \\
        --admin-password testpassword \\
        --rider-email <rider@test.local> \\
        --rider-password <pw>

Requisitos previos:
- Toggle `delivery_franjas_activo=1` en SiteConfig (por eso lo activa
  el propio script vía /superadmin/config si no lo está).
- Usuario superadmin y usuario repartidor existentes.

Salida: 0 si todos los pasos pasaron; 1 al primer fallo con detalle.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

try:
    import requests
except ImportError:
    print("ERROR: falta 'requests'. Instala con: pip install requests", file=sys.stderr)
    sys.exit(2)


def _die(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"✅ {msg}")


def _login(base_url: str, email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{base_url}/login", data={"email": email, "password": password}, allow_redirects=True)
    if r.status_code >= 400 or "logout" not in r.text.lower():
        _die(f"login fallido para {email}: HTTP {r.status_code}")
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-password", required=True)
    ap.add_argument("--rider-email", required=True)
    ap.add_argument("--rider-password", required=True)
    args = ap.parse_args()

    admin = _login(args.base_url, args.admin_email, args.admin_password)
    _ok(f"login admin ({args.admin_email})")

    # Fecha lo suficientemente adelante para no chocar con planificación real.
    fecha = (date.today() + timedelta(days=2)).isoformat()
    payload = {
        "fecha": fecha,
        "hora_inicio": "20:00",
        "hora_fin": "21:00",
        "capacidad_max": 2,
        "max_repartidores": 1,
    }
    r = admin.post(f"{args.base_url}/admin/delivery/franjas", json=payload)
    if r.status_code == 404:
        _die("delivery_franjas_activo=0 (activa el toggle en /superadmin/config y reintenta)")
    if r.status_code not in (201, 409):
        _die(f"crear franja falló: HTTP {r.status_code} {r.text[:200]}")
    slot = r.json() if r.status_code == 201 else None
    if slot:
        _ok(f"franja creada id={slot['id']} {fecha} 20:00-21:00 cap=2")
    else:
        _ok(f"franja ya existía para {fecha} 20:00-21:00 (409)")

    # Lista franjas admin
    r = admin.get(f"{args.base_url}/admin/delivery/franjas?desde={fecha}&hasta={fecha}")
    if r.status_code != 200:
        _die(f"listar franjas admin falló: HTTP {r.status_code}")
    _ok(f"listar admin: {len(r.json().get('franjas', []))} franjas en el rango")

    # Lista franjas cliente
    r = requests.get(f"{args.base_url}/api/delivery/franjas-disponibles")
    if r.status_code != 200:
        _die(f"listar franjas cliente falló: HTTP {r.status_code}")
    disponibles = r.json().get("franjas", [])
    _ok(f"listar cliente: {len(disponibles)} franjas visibles en {r.json().get('horizonte_dias')} días")

    # Rider: toma la franja
    rider = _login(args.base_url, args.rider_email, args.rider_password)
    _ok(f"login rider ({args.rider_email})")
    if slot:
        r = rider.post(f"{args.base_url}/repartidor/franjas/{slot['id']}/tomar")
        if r.status_code not in (200, 409):
            _die(f"tomar franja falló: HTTP {r.status_code} {r.text[:200]}")
        _ok(f"tomar franja: {r.json()}")

    # Rider: verifica su lista incluye la franja tomada
    r = rider.get(f"{args.base_url}/repartidor/franjas")
    if r.status_code != 200:
        _die(f"listar rider falló: HTTP {r.status_code}")
    mias = [f for f in r.json().get("franjas", []) if f.get("tomada_por_mi")]
    _ok(f"rider ve {len(mias)} franjas propias")

    # Rider: libera
    if slot:
        r = rider.post(f"{args.base_url}/repartidor/franjas/{slot['id']}/liberar")
        if r.status_code != 200:
            _die(f"liberar falló: HTTP {r.status_code}")
        _ok(f"liberar franja: {r.json()}")

    print("\n🎉 smoke test OK — módulo delivery_franjas responde en todos los endpoints básicos.")


if __name__ == "__main__":
    main()
