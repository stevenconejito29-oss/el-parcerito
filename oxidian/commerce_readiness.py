"""Diagnóstico de venta basado en las fuentes reales de configuración y datos."""
from __future__ import annotations

from datetime import timedelta

from models import DeliverySlot, KnowledgeEntry, Product, SiteConfig, User, WebChatConversation, ZonaEntrega
from business_time import business_today
from delivery_mode_service import modos_delivery_activos
from store_config import get_store_features, get_store_value


def commerce_readiness() -> dict:
    features = get_store_features()
    checks: list[dict] = []

    def add(key, label, ok, detail, action=None, warning=False):
        checks.append({
            "key": key, "label": label, "ok": bool(ok), "detail": detail,
            "action": action, "warning": bool(warning and not ok),
        })

    name = (SiteConfig.get("NOMBRE_NEGOCIO", "") or "").strip()
    add("brand", "Identidad de la tienda", bool(name),
        "Nombre comercial configurado." if name else "Falta el nombre comercial.", "superadmin.config")

    products = Product.query.filter_by(activo=True).count()
    add("catalog", "Catálogo publicable", products > 0,
        f"{products} producto(s) activo(s)." if products else "No hay productos activos.", "admin.productos")

    fulfillment = bool(features.get("delivery") or features.get("recogida"))
    add("fulfillment", "Forma de entrega", fulfillment,
        "Hay al menos una modalidad activa." if fulfillment else "Activa delivery o recogida.", "superadmin.dashboard")

    payments = [key for key in ("efectivo", "bizum", "tarjeta") if features.get(key)]
    add("payments", "Cobro al cliente", bool(payments),
        "Métodos activos: " + ", ".join(payments) if payments else "No existe ningún método de pago activo.", "superadmin.dashboard")

    if features.get("delivery"):
        zones = ZonaEntrega.query.filter_by(activo=True).count()
        add("zones", "Cobertura delivery", zones > 0,
            f"{zones} zona(s) activa(s)." if zones else "Delivery está activo pero no hay zonas de cobertura.", "superadmin.zonas")
        riders = User.query.filter_by(rol="repartidor", activo=True).count()
        add("riders", "Equipo de reparto", riders > 0,
            f"{riders} rider(s) activo(s)." if riders else "No hay riders activos.", "admin.usuarios")
        modes = modos_delivery_activos()
        add("delivery_mode", "Modalidad de reparto", any(modes.values()),
            "Instantáneo y franjas apagados." if not any(modes.values()) else "Modalidad operativa configurada.", "admin.delivery_franjas_panel")
        if modes.get("franjas") and not modes.get("inmediato"):
            try:
                horizon = max(1, int(get_store_value("delivery_franjas_horizonte_cliente_dias", "7") or 7))
            except (TypeError, ValueError):
                horizon = 7
            today = business_today()
            slots = DeliverySlot.query.filter(
                DeliverySlot.activo.is_(True), DeliverySlot.fecha >= today,
                DeliverySlot.fecha <= today + timedelta(days=horizon - 1),
            ).count()
            add("slots", "Franjas disponibles", slots > 0,
                f"{slots} franja(s) próximas." if slots else "Solo operas por franjas y no hay salidas próximas.", "admin.delivery_franjas_panel")

    kitchen = User.query.filter(User.rol.in_(("cocina", "preparacion")), User.activo.is_(True)).count()
    add("kitchen", "Equipo de preparación", kitchen > 0,
        f"{kitchen} usuario(s) operativo(s)." if kitchen else "No hay usuarios activos de cocina/preparación.", "admin.usuarios")

    support = User.query.filter(User.rol.in_(("admin", "super_admin")), User.activo.is_(True)).count()
    add("support", "Soporte web", support > 0,
        f"{support} agente(s) autorizado(s)." if support else "No hay agentes activos.", "superadmin.admins")

    knowledge = KnowledgeEntry.query.filter_by(activo=True).count()
    add("knowledge", "Conocimiento del chat", knowledge > 0,
        f"{knowledge} respuesta(s) editable(s)." if knowledge else "El chat conserva flujos base, pero no tiene FAQs personalizadas.",
        "superadmin.chatbot_faq_lista", warning=True)

    pending_chats = WebChatConversation.query.filter_by(status="waiting_agent").count()
    blockers = [row for row in checks if not row["ok"] and not row["warning"]]
    warnings = [row for row in checks if row["warning"]]
    return {
        "ready": not blockers, "checks": checks, "blockers": len(blockers),
        "warnings": len(warnings), "pending_chats": pending_chats,
    }
