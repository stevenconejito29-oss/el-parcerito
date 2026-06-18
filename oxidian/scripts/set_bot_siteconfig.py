#!/usr/bin/env python3
"""Set BOT_API_URL and BOT_API_KEY via Oxidian app context (ORM)
Usage: python set_bot_siteconfig.py --url http://localhost:3000 --key your-key
"""
import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # workspace root
OX_DIR = REPO_ROOT / 'oxidian'
sys.path.insert(0, str(OX_DIR))

from app import create_app

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True, help="Bot base URL, e.g. http://localhost:3000")
parser.add_argument("--key", required=True, help="Bot API key to trust")
parser.add_argument("--env", default="default", help="Flask config env (default)")
args = parser.parse_args()

app = create_app(env=args.env)

with app.app_context():
    from models import SiteConfig
    from extensions import db
    SiteConfig.set("BOT_API_URL", args.url)
    SiteConfig.set("BOT_API_KEY", args.key)
    db.session.commit()
    print("Wrote BOT_API_URL and BOT_API_KEY via ORM")
