#!/usr/bin/env python
"""
Local Flask server launcher for simulation.
Starts app in simulation mode (file SQLite with seeded data).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

SIM_DB_PATH = os.environ.get(
    "SIM_DATABASE_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "db", "oxidian_sim.db")),
)
os.makedirs(os.path.dirname(SIM_DB_PATH), exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{SIM_DB_PATH}"

from app import create_app

SIM_PORT = int(os.environ.get("SIM_PORT", "5000"))

print(f"Iniciando servidor Flask en http://localhost:{SIM_PORT}")
print(f"Modo: simulación (SQLite archivo: {SIM_DB_PATH})")

app = create_app("simulation")

# Seed initial data
with app.app_context():
    from extensions import db
    from seed_colombian_test_catalog import seed_colombian_catalog
    from sqlalchemy import text
    from models import AdminFeature, Product, SiteConfig, Stock, User

    if os.environ.get("SIM_RESET_DB", "1") != "0":
        db.drop_all()
    db.create_all()
    db.session.execute(text("PRAGMA journal_mode=WAL"))
    db.session.execute(text("PRAGMA busy_timeout=30000"))

    if Product.query.count() == 0:
        print("Seeding database...")
        seed_colombian_catalog()
        print("✓ Database seeded")
    else:
        print(f"✓ Database ready ({Product.query.count()} products)")

    for product in Product.query.filter_by(activo=True).all():
        if product.tipo_entrega == "inmediato" and not product.es_combo and product.stock_total < 5000:
            db.session.add(Stock(
                producto_id=product.id,
                cantidad=5000 - product.stock_total,
                lote="SIM-STRESS",
                ubicacion="Simulación",
            ))
    db.session.flush()

    bot_key = os.environ.get("SIM_BOT_KEY", "sim-bot-key")
    SiteConfig.set("BOT_API_KEY", bot_key)

    pos_email = os.environ.get("SIM_POS_EMAIL", "admin-sim@test.com")
    pos_password = os.environ.get("SIM_POS_PASSWORD", "sim1234")
    admin = User.query.filter_by(email=pos_email).first()
    if not admin:
        admin = User(nombre="Admin Simulación", email=pos_email, rol="admin", activo=True)
        admin.set_password(pos_password)
        db.session.add(admin)
    admin.rol = "admin"
    admin.activo = True
    admin.set_password(pos_password)
    db.session.flush()
    AdminFeature.inicializar_para_admin(admin.id, activar_todos=True)
    db.session.commit()
    print(f"✓ Bot API key lista para simulación ({bot_key})")
    print(f"✓ Admin POS listo para simulación ({pos_email})")

# Run
app.run(host="0.0.0.0", port=SIM_PORT, debug=True, use_reloader=False)
