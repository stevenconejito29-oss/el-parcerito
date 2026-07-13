"""Tests de la verificación pasiva antifraude de pedidos.

Cubre:
- `evaluate_order_risk`: puntúa LOW/MEDIUM/HIGH según historial + monto.
- `marcar_confirmacion_si_procede`: setea `confirmacion_estado='pending'`
  para riesgos MEDIUM/HIGH; deja LOW sin fricción.
- `marcar_pedido_confirmado`: idempotente y solo aplica sobre 'pending'.
- El endpoint `/api/bot/pedido/<id>/confirmar` respeta ownership por
  teléfono y devuelve códigos coherentes.
"""
import unittest

from flask import Flask

from extensions import db
from models import Order, SiteConfig, User


class OrderRiskConfirmationTest(unittest.TestCase):
    _seq_user = 0

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self._seq_order = 0

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_cliente(self, nombre="Cliente"):
        OrderRiskConfirmationTest._seq_user += 1
        seq = OrderRiskConfirmationTest._seq_user
        u = User(
            nombre=nombre,
            email=f"{nombre}-{seq}@test.invalid",
            telefono=f"+3460000{seq:05d}",
            rol="cliente",
            activo=True,
        )
        u.set_password("test")
        db.session.add(u)
        db.session.commit()
        return u

    def _mk_pedido(self, cliente, total=10, estado="pendiente"):
        self._seq_order += 1
        o = Order(
            numero_pedido=f"TEST-{self._seq_order:04d}",
            cliente_id=cliente.id,
            total=total,
            subtotal=total,
            estado=estado,
        )
        db.session.add(o)
        db.session.commit()
        return o

    # ── evaluate_order_risk ─────────────────────────────────────────

    def test_evaluate_low_cuando_cliente_conocido_y_monto_normal(self):
        from services import evaluate_order_risk
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        # Pedido histórico entregado
        self._mk_pedido(cliente, total=8, estado="entregado")
        pedido = self._mk_pedido(cliente, total=80)
        # Con historial y monto no-extremo (80 < 3*50=150) → LOW sin fricción
        result = evaluate_order_risk(pedido)
        self.assertEqual(result["level"], "LOW")

    def test_evaluate_high_para_primera_vez_del_cliente(self):
        # Regla del negocio: primera vez = siempre confirmar. Monto no importa.
        from services import evaluate_order_risk
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=10)  # monto pequeño
        result = evaluate_order_risk(pedido)
        self.assertEqual(result["level"], "HIGH")
        self.assertIn("cliente_sin_historial", result["reasons"])

    def test_evaluate_high_incluso_para_primera_vez_con_monto_alto(self):
        # Mismo resultado — la primera vez siempre es HIGH.
        from services import evaluate_order_risk
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=500)
        self.assertEqual(evaluate_order_risk(pedido)["level"], "HIGH")

    def test_evaluate_medium_cliente_conocido_con_monto_extremo(self):
        # Guardrail defensivo: cliente registrado pero pide 3x el umbral.
        # Puede indicar cuenta comprometida, pedido accidental o cambio de
        # comportamiento — MEDIUM para revisión, no bloqueo.
        from services import evaluate_order_risk
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        self._mk_pedido(cliente, total=8, estado="entregado")
        pedido = self._mk_pedido(cliente, total=200)  # 200 >= 3*50=150
        result = evaluate_order_risk(pedido)
        self.assertEqual(result["level"], "MEDIUM")
        self.assertIn("monto_extremo>=150", result["reasons"])

    def test_cliente_conocido_con_monto_medio_no_activa_medium(self):
        # Monto entre umbral y 3x umbral: LOW porque tiene historial.
        # Antes: 80 disparaba MEDIUM. Ahora: LOW (regla nueva del negocio).
        from services import evaluate_order_risk
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        self._mk_pedido(cliente, total=8, estado="entregado")
        pedido = self._mk_pedido(cliente, total=80)
        self.assertEqual(evaluate_order_risk(pedido)["level"], "LOW")

    def test_umbral_extremo_configurable_via_siteconfig(self):
        # Umbral 10 → 3x = 30. Monto 40 con historial → MEDIUM extremo.
        from services import evaluate_order_risk
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "10", descripcion="test")
        cliente = self._mk_cliente()
        self._mk_pedido(cliente, total=5, estado="entregado")
        pedido = self._mk_pedido(cliente, total=40)
        self.assertEqual(evaluate_order_risk(pedido)["level"], "MEDIUM")

    # ── marcar_confirmacion_si_procede ──────────────────────────────

    def test_marcar_no_toca_pedido_low(self):
        # Cliente conocido con monto normal → LOW → sin fricción.
        from services import marcar_confirmacion_si_procede
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        self._mk_pedido(cliente, total=8, estado="entregado")
        pedido = self._mk_pedido(cliente, total=80)
        level = marcar_confirmacion_si_procede(pedido)
        self.assertEqual(level, "LOW")
        self.assertIsNone(pedido.confirmacion_estado)

    def test_marcar_pending_para_medium(self):
        # Cliente conocido pero monto extremo (>=3x umbral) → MEDIUM.
        from services import marcar_confirmacion_si_procede
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        self._mk_pedido(cliente, total=8, estado="entregado")
        pedido = self._mk_pedido(cliente, total=200)
        marcar_confirmacion_si_procede(pedido)
        self.assertEqual(pedido.confirmacion_estado, "pending")
        self.assertEqual(pedido.confirmacion_nivel, "MEDIUM")

    def test_marcar_pending_para_high_primera_vez(self):
        # Regla del negocio: primera vez → SIEMPRE HIGH, sin importar monto.
        from services import marcar_confirmacion_si_procede
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=10)  # monto pequeño
        marcar_confirmacion_si_procede(pedido)
        self.assertEqual(pedido.confirmacion_estado, "pending")
        self.assertEqual(pedido.confirmacion_nivel, "HIGH")

    def test_marcar_respeta_interruptor_habilitada(self):
        from services import marcar_confirmacion_si_procede
        SiteConfig.set("CONFIRMACION_HABILITADA", "0", descripcion="test")
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=200)
        level = marcar_confirmacion_si_procede(pedido)
        self.assertIsNone(level)
        self.assertIsNone(pedido.confirmacion_estado)

    def test_marcar_es_idempotente(self):
        from services import marcar_confirmacion_si_procede
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=200)
        marcar_confirmacion_si_procede(pedido)
        # Segunda pasada no debe sobrescribir (aunque marcaría lo mismo)
        prev = pedido.confirmacion_estado
        level = marcar_confirmacion_si_procede(pedido)
        self.assertIsNone(level)
        self.assertEqual(pedido.confirmacion_estado, prev)

    # ── marcar_pedido_confirmado ────────────────────────────────────

    def test_confirmado_solo_aplica_sobre_pending(self):
        from services import marcar_pedido_confirmado
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=15)
        # Sin confirmacion pendiente → no aplica
        self.assertFalse(marcar_pedido_confirmado(pedido))
        pedido.confirmacion_estado = "pending"
        self.assertTrue(marcar_pedido_confirmado(pedido))
        self.assertEqual(pedido.confirmacion_estado, "confirmed")
        self.assertIsNotNone(pedido.confirmacion_en)

    def test_confirmado_idempotente_segunda_llamada(self):
        from services import marcar_pedido_confirmado
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=15)
        pedido.confirmacion_estado = "pending"
        self.assertTrue(marcar_pedido_confirmado(pedido))
        # Segunda llamada retorna False porque ya está confirmed
        self.assertFalse(marcar_pedido_confirmado(pedido))

    # ── Enriquecido del mensaje de estado ───────────────────────────

    def test_mensaje_pendiente_incluye_invitacion_a_confirmar(self):
        from services import mensaje_estado_pedido
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=80)
        pedido.confirmacion_estado = "pending"
        msg = mensaje_estado_pedido(pedido)
        self.assertIn("SI", msg)
        self.assertIn("NO", msg)
        self.assertIn(pedido.numero_pedido, msg)

    def test_mensaje_pendiente_sin_confirmacion_no_incluye_invitacion(self):
        from services import mensaje_estado_pedido
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=10)
        # sin confirmacion_estado → LOW normal → sin invitación
        msg = mensaje_estado_pedido(pedido)
        self.assertNotIn("Responde *SI*", msg)

    def test_mensaje_armando_ignora_confirmacion(self):
        # En estados posteriores a `pendiente` no debe aparecer la invitación
        # aunque confirmacion_estado siga en 'pending' — la puerta de entrada
        # ya se cerró y no queremos duplicar CTAs.
        from services import mensaje_estado_pedido
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=80, estado="armando")
        pedido.confirmacion_estado = "pending"
        msg = mensaje_estado_pedido(pedido)
        self.assertNotIn("Responde *SI*", msg)

    # ── Persistencia de la columna (regresión de la migración) ──────

    def test_confirmacion_estado_persiste_en_orm(self):
        # Asegura que el mapeo SQLAlchemy escribe y lee la nueva columna,
        # y que confirmacion_en también viaja.
        from datetime import datetime
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=15)
        pedido.confirmacion_estado = "pending"
        pedido.confirmacion_en = datetime(2026, 7, 12, 22, 0, 0)
        db.session.commit()
        db.session.expire(pedido)
        refetched = Order.query.get(pedido.id)
        self.assertEqual(refetched.confirmacion_estado, "pending")
        self.assertEqual(refetched.confirmacion_en.year, 2026)

    # ── metricas_antifraude ─────────────────────────────────────────

    def test_metricas_vacio_devuelve_ceros_y_tasa_none(self):
        from services import metricas_antifraude
        m = metricas_antifraude(dias=30)
        self.assertEqual(m["evaluados"], 0)
        self.assertEqual(m["confirmados"], 0)
        self.assertEqual(m["cancelados_por_bot"], 0)
        self.assertIsNone(m["tasa_confirmacion"])

    def test_metricas_cuenta_confirmados_y_pending(self):
        from services import metricas_antifraude
        cliente = self._mk_cliente()
        p1 = self._mk_pedido(cliente, total=10)
        p1.confirmacion_estado = "pending"
        p2 = self._mk_pedido(cliente, total=20)
        p2.confirmacion_estado = "confirmed"
        p3 = self._mk_pedido(cliente, total=30)
        # p3 LOW → sin confirmacion_estado, no debe contar como evaluado.
        db.session.commit()
        m = metricas_antifraude(dias=30)
        self.assertEqual(m["evaluados"], 2)  # p1 + p2
        self.assertEqual(m["confirmados"], 1)  # p2
        self.assertEqual(m["pending_vigentes"], 1)  # p1

    def test_metricas_no_cuenta_pending_de_pedidos_cancelados(self):
        # Un pedido que quedó marcado 'pending' pero luego se cancela por
        # cualquier vía NO debe seguir figurando como "pending_vigente":
        # es una cola de riesgo actualmente actionable.
        from services import metricas_antifraude
        cliente = self._mk_cliente()
        p = self._mk_pedido(cliente, total=10, estado="cancelado")
        p.confirmacion_estado = "pending"
        db.session.commit()
        m = metricas_antifraude(dias=30)
        self.assertEqual(m["pending_vigentes"], 0)

    def test_marcar_guarda_nivel_medium_cuando_conocido_con_monto_extremo(self):
        # Cliente conocido pero monto extremo (>=3x umbral) → MEDIUM.
        from services import marcar_confirmacion_si_procede
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        self._mk_pedido(cliente, total=8, estado="entregado")
        pedido = self._mk_pedido(cliente, total=200)  # 200 >= 150 (3x50) → MEDIUM
        marcar_confirmacion_si_procede(pedido)
        self.assertEqual(pedido.confirmacion_estado, "pending")
        self.assertEqual(pedido.confirmacion_nivel, "MEDIUM")

    def test_marcar_guarda_nivel_high_para_primera_vez(self):
        # Regla del negocio: primera vez → HIGH, sin importar monto.
        from services import marcar_confirmacion_si_procede
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        pedido = self._mk_pedido(cliente, total=200)
        marcar_confirmacion_si_procede(pedido)
        self.assertEqual(pedido.confirmacion_estado, "pending")
        self.assertEqual(pedido.confirmacion_nivel, "HIGH")

    def test_marcar_low_no_setea_nivel(self):
        # Cliente conocido con monto no-extremo → LOW → sin fricción.
        from services import marcar_confirmacion_si_procede
        SiteConfig.set("CONFIRMACION_MONTO_UMBRAL_EUR", "50", descripcion="test")
        cliente = self._mk_cliente()
        self._mk_pedido(cliente, total=10, estado="entregado")
        pedido = self._mk_pedido(cliente, total=80)  # con historial + monto normal → LOW
        marcar_confirmacion_si_procede(pedido)
        self.assertIsNone(pedido.confirmacion_estado)
        self.assertIsNone(pedido.confirmacion_nivel)

    # ── autocancelar_confirmaciones_expiradas ──────────────────────

    def _configurar_ttl(self, minutos):
        SiteConfig.set("CONFIRMACION_TTL_HIGH_MINUTES", str(minutos), descripcion="test")
        db.session.commit()

    def _mk_pedido_pending(self, cliente, nivel, minutos_atras, estado="pendiente"):
        from datetime import timedelta
        from services import utcnow
        p = self._mk_pedido(cliente, total=100, estado=estado)
        p.confirmacion_estado = "pending"
        p.confirmacion_nivel = nivel
        p.creado_en = utcnow() - timedelta(minutes=minutos_atras)
        db.session.commit()
        return p

    def test_autocancel_no_toca_high_reciente(self):
        from services import autocancelar_confirmaciones_expiradas
        self._configurar_ttl(120)  # 2h TTL
        cliente = self._mk_cliente()
        p = self._mk_pedido_pending(cliente, "HIGH", minutos_atras=30)
        r = autocancelar_confirmaciones_expiradas()
        self.assertEqual(r["procesados"], 0)
        db.session.expire(p)
        self.assertEqual(p.estado, "pendiente")

    def test_autocancel_cancela_high_expirado(self):
        from services import autocancelar_confirmaciones_expiradas
        self._configurar_ttl(60)  # 1h TTL
        cliente = self._mk_cliente()
        p = self._mk_pedido_pending(cliente, "HIGH", minutos_atras=90)
        r = autocancelar_confirmaciones_expiradas()
        self.assertEqual(r["procesados"], 1)
        self.assertEqual(r["cancelados"], 1)
        db.session.expire(p)
        self.assertEqual(p.estado, "cancelado")

    def test_autocancel_no_toca_medium_aunque_expire(self):
        # MEDIUM se queda pending para revisión manual — nunca auto-cancela.
        from services import autocancelar_confirmaciones_expiradas
        self._configurar_ttl(60)
        cliente = self._mk_cliente()
        p = self._mk_pedido_pending(cliente, "MEDIUM", minutos_atras=300)
        r = autocancelar_confirmaciones_expiradas()
        self.assertEqual(r["procesados"], 0)
        db.session.expire(p)
        self.assertEqual(p.estado, "pendiente")

    def test_autocancel_no_toca_high_que_ya_esta_armando(self):
        # Si el equipo ya empezó a preparar, no interferimos.
        from services import autocancelar_confirmaciones_expiradas
        self._configurar_ttl(60)
        cliente = self._mk_cliente()
        p = self._mk_pedido_pending(cliente, "HIGH", minutos_atras=90, estado="armando")
        r = autocancelar_confirmaciones_expiradas()
        self.assertEqual(r["procesados"], 0)
        db.session.expire(p)
        self.assertEqual(p.estado, "armando")

    def test_autocancel_desactivado_con_ttl_cero(self):
        from services import autocancelar_confirmaciones_expiradas
        self._configurar_ttl(0)
        cliente = self._mk_cliente()
        self._mk_pedido_pending(cliente, "HIGH", minutos_atras=9999)
        r = autocancelar_confirmaciones_expiradas()
        self.assertEqual(r["procesados"], 0)
        self.assertEqual(r["cancelados"], 0)

    def test_autocancel_cap_defensivo_ttl_minimo_15min(self):
        # TTL=5 (por debajo del cap) → se sube a 15 internamente.
        from services import autocancelar_confirmaciones_expiradas
        self._configurar_ttl(5)
        cliente = self._mk_cliente()
        # Pedido de 10 min atrás no expira (por debajo del cap 15).
        p1 = self._mk_pedido_pending(cliente, "HIGH", minutos_atras=10)
        # Pedido de 20 min atrás sí expira.
        p2 = self._mk_pedido_pending(cliente, "HIGH", minutos_atras=20)
        r = autocancelar_confirmaciones_expiradas()
        self.assertEqual(r["cancelados"], 1)
        db.session.expire(p1); db.session.expire(p2)
        self.assertEqual(p1.estado, "pendiente")
        self.assertEqual(p2.estado, "cancelado")

    def test_autocancel_evento_tiene_detalle_identificable(self):
        # `metricas_antifraude` cuenta rechazados_por_bot buscando la
        # palabra clave "verificación pasiva". El auto-cancel debe usar
        # un detalle que no se confunda con un rechazo real del cliente.
        from services import autocancelar_confirmaciones_expiradas
        from models import OrderEvent
        self._configurar_ttl(60)
        cliente = self._mk_cliente()
        p = self._mk_pedido_pending(cliente, "HIGH", minutos_atras=90)
        autocancelar_confirmaciones_expiradas()
        eventos = OrderEvent.query.filter_by(pedido_id=p.id, tipo="pedido_cancelado").all()
        self.assertEqual(len(eventos), 1)
        self.assertIn("auto-cancelado", eventos[0].detalle.lower())
        self.assertIn("high", eventos[0].detalle.lower())

    def test_metricas_desagregan_por_nivel(self):
        from services import metricas_antifraude
        cliente = self._mk_cliente()
        # 2 MEDIUM, 1 HIGH
        for _ in range(2):
            p = self._mk_pedido(cliente, total=15)
            p.confirmacion_estado = "pending"
            p.confirmacion_nivel = "MEDIUM"
        p2 = self._mk_pedido(cliente, total=200)
        p2.confirmacion_estado = "pending"
        p2.confirmacion_nivel = "HIGH"
        db.session.commit()
        m = metricas_antifraude(dias=30)
        self.assertEqual(m["por_nivel"]["MEDIUM"], 2)
        self.assertEqual(m["por_nivel"]["HIGH"], 1)
        self.assertEqual(m["evaluados"], 3)

    def test_metricas_tasa_confirmacion_calculada(self):
        # 3 confirmados + 1 rechazado por bot (evento) → tasa 75%.
        from services import metricas_antifraude
        from models import OrderEvent
        cliente = self._mk_cliente()
        for _ in range(3):
            p = self._mk_pedido(cliente, total=10)
            p.confirmacion_estado = "confirmed"
        # 1 evento de cancelación por verificación pasiva
        pc = self._mk_pedido(cliente, total=10, estado="cancelado")
        pc.confirmacion_estado = "pending"  # antes de cancelar
        ev = OrderEvent(
            pedido_id=pc.id,
            tipo="pedido_cancelado",
            estado_anterior="pendiente",
            estado_nuevo="cancelado",
            canal="chatbot",
            detalle="cliente respondió NO a la verificación pasiva",
        )
        db.session.add(ev)
        db.session.commit()
        m = metricas_antifraude(dias=30)
        self.assertEqual(m["confirmados"], 3)
        self.assertEqual(m["cancelados_por_bot"], 1)
        self.assertAlmostEqual(m["tasa_confirmacion"], 0.75, places=2)


if __name__ == "__main__":
    unittest.main()
