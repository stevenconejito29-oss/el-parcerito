"""Textos públicos de seguimiento; no modifica estados ni reglas de negocio."""


def order_presentation(order):
    from models import normalizar_metodo_pago

    pending = order.confirmacion_estado == "pending"
    delivery = (order.tipo_entrega_cliente or "delivery") == "delivery"
    titles = {
        "pendiente": "Pedido recibido",
        "armando": "Estamos preparando tu pedido",
        "listo": "Listo para el reparto" if delivery else "Listo para recoger",
        "en_ruta": "Tu pedido está en camino",
        "entregado": "Pedido finalizado",
        "cancelado": "Pedido cancelado",
    }
    descriptions = {
        "pendiente": "Puedes consultar aquí el estado de tu pedido.",
        "armando": "El equipo está preparando los productos de tu pedido.",
        "listo": "El pedido está preparado para su salida de reparto." if delivery else "Tu pedido está preparado. Consulta abajo los datos de recogida.",
        "en_ruta": "El repartidor lleva tu pedido. Ten disponible el código de entrega.",
        "entregado": "La entrega ha finalizado. Gracias por tu compra.",
        "cancelado": "Este pedido ha sido cancelado.",
    }
    if order.estado in {"cancelado", "entregado"}:
        pending = False
    payment = normalizar_metodo_pago(getattr(order, "metodo_pago", None))
    payment_confirmed = bool(getattr(order, "pago_confirmado", False))
    labels = {
        "pendiente": "Recibido", "armando": "En preparación", "listo": "Listo",
        "en_ruta": "En reparto", "entregado": "Finalizado", "cancelado": "Cancelado",
    }
    return {
        "confirmation_pending": pending,
        "status_label": "Esperando confirmación" if pending else labels.get(order.estado, "Estado por consultar"),
        "payment_label": {"efectivo": "Efectivo", "bizum": "Bizum", "tarjeta": "Tarjeta"}.get(payment, "Método por confirmar"),
        "payment_status": "Pago confirmado" if payment_confirmed else "Pago pendiente de confirmar",
        "payment_confirmed": payment_confirmed,
        "fulfillment_label": "Delivery" if delivery else "Recogida",
        "title": "Confirma tu pedido" if pending else titles.get(order.estado, "Estado de tu pedido"),
        "description": "Confirma por WhatsApp para que el equipo pueda empezar a prepararlo." if pending else descriptions.get(order.estado, "Consulta los detalles de tu pedido."),
    }
