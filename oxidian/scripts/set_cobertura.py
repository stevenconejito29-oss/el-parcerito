#!/usr/bin/env python3
"""Configura el perímetro único de entrega (centro + radio) vía ORM.

Uso:
    python set_cobertura.py --lat 37.4711 --lon -5.6422 --radio 15
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OX_DIR = REPO_ROOT / "oxidian"
sys.path.insert(0, str(OX_DIR))

from app import create_app

parser = argparse.ArgumentParser()
parser.add_argument("--lat", required=True, type=float)
parser.add_argument("--lon", required=True, type=float)
parser.add_argument("--radio", required=True, type=float, help="Radio en km")
parser.add_argument("--env", default="default")
args = parser.parse_args()

if not (-90 <= args.lat <= 90):
    sys.exit("lat fuera de rango")
if not (-180 <= args.lon <= 180):
    sys.exit("lon fuera de rango")
if not (0 < args.radio <= 200):
    sys.exit("radio fuera de rango (0-200 km)")

app = create_app(env=args.env)
with app.app_context():
    from models import SiteConfig
    from extensions import db

    SiteConfig.set("CENTRO_LAT", f"{args.lat:.6f}")
    SiteConfig.set("CENTRO_LON", f"{args.lon:.6f}")
    SiteConfig.set("RADIO_ENTREGA_KM", str(args.radio))
    SiteConfig.set("VALIDAR_RADIO_ENTREGA", "1")
    SiteConfig.set("BLOQUEAR_DIRECCION_NO_VERIFICADA", "1")
    db.session.commit()
    print(
        f"OK · CENTRO_LAT={args.lat:.6f} CENTRO_LON={args.lon:.6f} "
        f"RADIO_ENTREGA_KM={args.radio}"
    )
