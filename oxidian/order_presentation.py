"""Textos públicos de seguimiento; no modifica estados ni reglas de negocio."""


def order_presentation(order):
    from models import normalizar_metodo_pago

    pending = order.confirmacion_estado == "pending"
    delivery = (order.tipo_entrega_cliente or "delivery") == "delivery"
    titles = {
        "pendiente": "Pedido recibido",
        "armando": "Estamos preparando tu pedido",
        "listo": "Listo para el reparto" if delivery else "Listo para recoger",
        "en_ruta": "Tu pedido está en camino" if delivery else "Listo para recoger",
        "entregado": "Pedido finalizado" if delivery else "Pedido recogido",
        "cancelado": "Pedido cancelado",
    }
    descriptions = {
        "pendiente": "Puedes consultar aquí el estado de tu pedido.",
        "armando": "El equipo está preparando los productos de tu pedido.",
        "listo": "El pedido está preparado para su salida de reparto." if delivery else "Ya puedes pasar a recogerlo. Muestra tu número de pedido y paga en el mostrador al recibirlo.",
        "en_ruta": "El repartidor lleva tu pedido. Ten disponible el código de entrega." if delivery else "Tu pedido espera en el negocio. Muestra el número de pedido al recogerlo.",
        "entregado": "La entrega ha finalizado. Gracias por tu compra.",
        "cancelado": "Este pedido ha sido cancelado.",
    }
    if order.estado in {"cancelado", "entregado"}:
        pending = False
    payment = normalizar_metodo_pago(getattr(order, "metodo_pago", None))
    payment_confirmed = bool(getattr(order, "pago_confirmado", False))
    labels = {
        "pendiente": "Recibido", "armando": "En preparación", "listo": "Listo para el reparto" if delivery else "Listo para recoger",
        "en_ruta": "En reparto" if delivery else "Listo para recoger", "entregado": "Finalizado" if delivery else "Recogido", "cancelado": "Cancelado",
    }
    stage = {"pendiente": 1, "armando": 2, "listo": 3,
             "en_ruta": 4 if delivery else 3, "entregado": 5 if delivery else 4}.get(order.estado, 0)
    return {
        "stage": stage,
        "requires_delivery": delivery,
        "pickup_ready": not delivery and order.estado in {"listo", "en_ruta"} and not pending,
        "confirmation_pending": pending,
        "status_label": "Esperando confirmación" if pending else labels.get(order.estado, "Estado por consultar"),
        "payment_label": {"efectivo": "Efectivo", "bizum": "Bizum", "tarjeta": "Tarjeta"}.get(payment, "Método por confirmar"),
        "payment_status": "Pago confirmado" if payment_confirmed else "Pago pendiente de confirmar",
        "payment_confirmed": payment_confirmed,
        "fulfillment_label": ("Reparto por franja" if getattr(order, "slot_id", None) else "Reparto a domicilio") if delivery else "Recogida en el negocio",
        "title": "Confirma tu pedido" if pending else titles.get(order.estado, "Estado de tu pedido"),
        "description": "Confirma por WhatsApp para que el equipo pueda empezar a prepararlo." if pending else descriptions.get(order.estado, "Consulta los detalles de tu pedido."),
    }
