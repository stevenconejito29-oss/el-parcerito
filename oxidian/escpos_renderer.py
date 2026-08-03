"""Genera bytes ESC/POS de un ticket a partir del modelo ``Order``.

Se usa cuando la impresora térmica está enchufada por cable USB a la
tablet que sirve como caja. El navegador (Chrome/Chromium en Android)
puede empujar estos bytes directo al endpoint OUT del USB con WebUSB,
sin pasar por CUPS ni red — pero el layout, sanitización y formato
deben calcularse en el servidor para mantener la lógica en un solo
sitio (comisiones, notas, alérgenos, snapshots del pedido).

El layout replica el ``pos/ticket.html`` con las mismas secciones y
prioridades: número de pedido grande, tipo de entrega destacado,
listado de items con extras/sabores/notas, TOTAL, método de pago,
puntos ganados. Al no depender de PDF ni de rasterizador, la impresión
es instantánea y siempre nítida.

Ancho lógico: 32 caracteres a fuente A (48 mm / 12 pt/char). Se ajusta
automáticamente cuando el nombre de producto es largo con envoltura
sencilla por palabras.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from escpos.printer import Dummy


# Ancho útil de la impresora ZJ-58 a fuente A (Font A = 12x24 dots).
# Papel 58 mm; cabezal 48 mm útiles; 384 dots / 12 = 32 caracteres.
COLS_FONT_A = 32
# Fuente B (más pequeña, 9x17) da 42 caracteres útiles.
COLS_FONT_B = 42


def _fmt_money(value) -> str:
    try:
        d = Decimal(str(value or 0))
    except Exception:
        d = Decimal("0")
    return f"{d:.2f}"


def _wrap(text: str, cols: int) -> list[str]:
    """Envoltura simple respetando palabras — mejor que ``textwrap.wrap`` para
    nombres con caracteres UTF-8, ya que la impresora los cuenta por byte."""
    words = (text or "").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= cols:
            cur = f"{cur} {w}"
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _line_row(izq: str, der: str, cols: int = COLS_FONT_A) -> str:
    """Genera una línea con ``izq`` alineado izquierda y ``der`` a la derecha.
    Si la suma no cabe, corta la izquierda para que el importe siempre se lea."""
    der = der.strip()
    if len(der) >= cols - 2:
        return der[:cols]
    max_izq = cols - len(der) - 1
    izq = (izq or "").strip()
    if len(izq) > max_izq:
        izq = izq[: max_izq - 1] + "…"
    return f"{izq}{' ' * (cols - len(izq) - len(der))}{der}"


def _sep_dashes(cols: int = COLS_FONT_A) -> str:
    return "-" * cols


def render_ticket(pedido, es_reimpresion: bool = False, brand=None, ui=None) -> bytes:
    """Devuelve los bytes ESC/POS listos para enviar al puerto OUT de la
    impresora térmica. No abre ningún dispositivo — es ``Dummy`` puro."""
    p = Dummy()
    cols = COLS_FONT_A

    # ── Inicialización + charset latino ────────────────────────────────
    p._raw(b"\x1b@")            # ESC @  reset
    p._raw(b"\x1bt\x10")        # ESC t 16  → codepage WPC1252 (latino-1)
    p._raw(b"\x1bR\x07")        # ESC R 7   → juego de caracteres España

    # ── Cabecera reimpresión / cancelado ───────────────────────────────
    if es_reimpresion:
        p.set(align="center", bold=True, width=1, height=1)
        p.text("*** REIMPRESION ***\n")
        p.set(bold=False)
        p.text(f"Estado: {(pedido.estado or '').upper()}\n")
        p.text(_sep_dashes(cols) + "\n")
    if pedido.estado == "cancelado":
        p.set(align="center", bold=True)
        p.text("*** CANCELADO ***\n")
        p.text("NO PREPARAR\n")
        p.text(_sep_dashes(cols) + "\n")

    # ── Marca ──────────────────────────────────────────────────────────
    p.set(align="center", bold=True, width=1, height=1)
    nombre_marca = (
        (brand.get("nombre") if isinstance(brand, dict) else None)
        or "EL PARCERITO"
    )
    p.text(f"{nombre_marca}\n")
    p.set(bold=False)
    ciudad = (
        (brand.get("ciudad") if isinstance(brand, dict) else None)
        or "Carmona, Sevilla"
    )
    if ciudad:
        p.text(f"{ciudad}\n")
    telefono_marca = (
        brand.get("telefono") if isinstance(brand, dict) else None
    )
    if telefono_marca:
        p.text(f"{telefono_marca}\n")
    p.text(_sep_dashes(cols) + "\n")

    # ── Tipo de entrega (destacado) ────────────────────────────────────
    tipo = (getattr(pedido, "tipo_entrega_cliente", None) or "recogida").lower()
    tipo_label = {
        "delivery": "DELIVERY",
        "recogida": "RECOGIDA",
        "programado": "PROGRAMADO",
    }.get(tipo, tipo.upper())
    p.set(align="center", bold=True, custom_size=True, width=2, height=2)
    p.text(f"{tipo_label}\n")
    p.set(align="center", custom_size=True, width=1, height=1, bold=False)
    p.text(_sep_dashes(cols) + "\n")

    # ── Número de pedido (XL — es lo que más consulta cocina/repartidor) ──
    p.set(align="center", bold=True)
    p.text("PEDIDO\n")
    # ¡CLAVE! python-escpos IGNORA width/height si no pasas custom_size=True.
    # width=5, height=5 = 5x el tamaño base → letras de ~15 mm de alto
    # (Font A base = 3 mm). Para números tipo "#1006" (5 chars a 60 dots
    # cada uno = 300 dots) cabe holgadamente en los 384 dots útiles del
    # cabezal ZJ-58 (48 mm).
    p.set(align="center", bold=True, custom_size=True, width=5, height=5)
    p.text(f"{pedido.numero_pedido}\n")
    p.set(align="center", custom_size=True, width=1, height=1, bold=False)
    try:
        fecha = pedido.creado_en.strftime("%d/%m/%Y  %H:%M")
    except Exception:
        fecha = ""
    if fecha:
        p.text(f"{fecha}\n")
    cajero = getattr(getattr(pedido, "cajero", None), "nombre", None)
    if cajero:
        p.text(f"Atendio: {cajero}\n")
    p.text(_sep_dashes(cols) + "\n")

    # ── Cliente ────────────────────────────────────────────────────────
    cliente = getattr(pedido, "cliente", None)
    if cliente:
        p.set(align="left", bold=False)
        nombre_cli = getattr(cliente, "nombre", "") or ""
        if nombre_cli:
            p.text(_line_row("Cliente", nombre_cli, cols) + "\n")
        tel = getattr(cliente, "telefono", None)
        if tel:
            p.text(_line_row("Tel", tel, cols) + "\n")
        direccion = getattr(pedido, "direccion_entrega", None)
        if tipo == "delivery" and direccion:
            for linea in _wrap(f"Entrega: {direccion}", cols):
                p.text(linea + "\n")
        zona = getattr(pedido, "zona_nombre_aplicada", None)
        if zona:
            p.text(_line_row("Zona", zona, cols) + "\n")
        p.text(_sep_dashes(cols) + "\n")

    # ── Items ──────────────────────────────────────────────────────────
    p.set(align="left")
    for item in getattr(pedido, "items", []) or []:
        nombre = getattr(getattr(item, "producto", None), "nombre", "?") or "?"
        cantidad = int(getattr(item, "cantidad", 1) or 1)
        subtotal = _fmt_money(getattr(item, "subtotal", 0))
        precio_unit = _fmt_money(getattr(item, "precio_unit", 0))
        header = f"{cantidad}x {nombre}"
        for linea in _wrap(header, cols - 8):
            p.text(_line_row(linea, "", cols) + "\n" if linea != _wrap(header, cols - 8)[-1] else _line_row(linea, f"{subtotal}", cols) + "\n")
        if float(precio_unit) > 0 and cantidad > 1:
            p.text(_line_row(f"  @ {precio_unit} EUR/ud", "", cols) + "\n")

        # Metadatos opcionales — sabores, extras, notas, alérgenos.
        meta = {}
        try:
            meta = item.get_metadata() or {}
        except Exception:
            meta = {}
        pres = (meta.get("presentacion") or {}) if meta else {}
        if pres.get("tamaño"):
            extra = pres.get("extra") or 0
            extra_txt = ""
            if extra and float(extra) > 0:
                extra_txt = f" (+{_fmt_money(extra)})"
            elif extra and float(extra) < 0:
                extra_txt = f" (-{_fmt_money(abs(float(extra)))})"
            p.text(f"  Tamano: {pres['tamaño']}{extra_txt}\n")
        flavors = ((meta.get("sabores") or {}).get("opciones") or [])
        if flavors:
            nombres = ", ".join(
                f"{f.get('nombre','?')}"
                + (f" x{f.get('cantidad')}" if int(f.get('cantidad', 1)) > 1 else "")
                for f in flavors
            )
            for linea in _wrap(f"  Sabores: {nombres}", cols):
                p.text(linea + "\n")
        extras = ((meta.get("extras") or {}).get("opciones") or [])
        if extras:
            nombres = ", ".join(
                f"{int(e.get('cantidad', 1))}x {e.get('nombre','?')}" for e in extras
            )
            for linea in _wrap(f"  Extras: {nombres}", cols):
                p.text(linea + "\n")
        if getattr(item, "notas", None):
            for linea in _wrap(f"  Nota: {item.notas}", cols):
                p.set(bold=True)
                p.text(linea + "\n")
                p.set(bold=False)

    p.text(_sep_dashes(cols) + "\n")

    # ── Totales ────────────────────────────────────────────────────────
    subtotal = _fmt_money(getattr(pedido, "subtotal", 0))
    p.text(_line_row("Subtotal", f"{subtotal} EUR", cols) + "\n")
    descuento = getattr(pedido, "descuento", None)
    if descuento and float(descuento) > 0:
        p.text(_line_row("Descuento", f"-{_fmt_money(descuento)} EUR", cols) + "\n")
    envio = getattr(pedido, "costo_envio_aplicado", 0)
    if envio and float(envio) > 0.001:
        p.text(_line_row("Envio", f"{_fmt_money(envio)} EUR", cols) + "\n")

    # ── TOTAL (doble alto para destacar) ───────────────────────────────
    total = _fmt_money(getattr(pedido, "total", 0))
    p.text(_sep_dashes(cols) + "\n")
    p.set(align="left", bold=True, custom_size=True, width=1, height=2)
    p.text(_line_row("TOTAL", f"{total} EUR", cols) + "\n")
    p.set(align="left", custom_size=True, width=1, height=1, bold=False)

    # ── Método de pago (destacado) ─────────────────────────────────────
    metodo = str(getattr(pedido, "metodo_pago", "") or "").upper() or "?"
    p.text(_sep_dashes(cols) + "\n")
    p.set(align="left", bold=True, custom_size=True, width=1, height=2)
    p.text(_line_row("PAGO", metodo, cols) + "\n")
    p.set(align="left", custom_size=True, width=1, height=1, bold=False)
    if metodo == "BIZUM":
        confirmado = bool(getattr(pedido, "pago_confirmado", False))
        p.text(
            "Estado: "
            + ("Confirmado" if confirmado else "Pendiente")
            + "\n"
        )
    p.text(_sep_dashes(cols) + "\n")

    # ── Puntos ganados ─────────────────────────────────────────────────
    puntos_g = getattr(pedido, "puntos_ganados", 0) or 0
    if brand and (brand.get("puntos") if isinstance(brand, dict) else False) and puntos_g > 0:
        p.set(align="center", bold=True)
        unit = (ui.get("loyalty_unit_plural") if isinstance(ui, dict) else None) or "puntos"
        p.text(f"+{puntos_g} {unit} Club\n")
        p.set(bold=False)
        p.text(_sep_dashes(cols) + "\n")

    # ── Notas del pedido ───────────────────────────────────────────────
    if getattr(pedido, "notas", None):
        p.set(align="left", bold=True)
        for linea in _wrap(f"Notas: {pedido.notas}", cols):
            p.text(linea + "\n")
        p.set(bold=False)
        p.text(_sep_dashes(cols) + "\n")

    # ── Cierre ─────────────────────────────────────────────────────────
    p.set(align="center")
    p.text("Gracias por tu compra\n")
    if fecha:
        p.text(fecha + "\n")

    # ── Feed + corte ───────────────────────────────────────────────────
    p._raw(b"\n\n\n")
    p._raw(b"\x1dV\x00")  # GS V 0 → full cut

    return p.output
