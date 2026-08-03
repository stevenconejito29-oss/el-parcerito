"""Persistencia atómica del builder de combos (admin y socio compartidos).

Recibe un ``Product`` (combo) con ID ya asignado y una lista de
``ComponenteInput`` validados por ``combo_form_parser.parse_componentes``.
En modo EDIT limpia ComboItem+ComboGroup existentes y reinserta en la misma
transacción (CASCADE limpia M2M). No hace commit — el caller decide cuándo.

Fuente única de verdad para admin (`routes/admin.py:nuevo_combo`) y socio
(`routes/proveedor.py:registrar_combo`).
"""

from __future__ import annotations

from typing import Optional


def _combo_group_name(tipo: str, nombre: Optional[str] = None) -> str:
    nombre = (nombre or "").strip()
    if nombre:
        return nombre[:80]
    return "Base incluida" if tipo == "fijo" else "Eleccion"


def build_combo(
    combo,
    componentes,
    *,
    group_defs: Optional[dict] = None,
    autor_role: Optional[str] = None,
    actor_user_id: Optional[int] = None,
):
    """Reconstruye la composición del combo dentro de la transacción actual.

    - Borra ``ComboItem`` + ``ComboGroup`` previos del combo (idempotente en edit).
    - Crea ``ComboGroup`` según ``group_defs`` (por uid, opcional) y usa
      ``__fixed__``/nombre-casefold como fallback para los componentes sin uid.
    - Inserta ``ComboItem`` con todos los campos (variant/presentation/flavor/M2M).
    - Si ``autor_role == "socio_producto"``: marca el combo como pending y setea
      partner_submitted_by/at, limpia review_by/at/note, y desactiva el combo.

    Devuelve la lista de ``ComboItem`` recién creados en el mismo orden que
    ``componentes``.
    """
    from extensions import db
    from models import (
        ComboGroup,
        ComboItem,
        ProductExtraOption,
        ProductPresentation,
        utcnow,
    )

    group_defs = group_defs or {}

    # ── Limpieza idempotente de composición previa ──
    ComboItem.query.filter_by(combo_id=combo.id).delete(synchronize_session=False)
    ComboGroup.query.filter_by(combo_id=combo.id).delete(synchronize_session=False)
    db.session.flush()

    # ── Crear grupos declarados por uid (si el UI los envió) ──
    groups_by_uid: dict = {}
    for uid, data in group_defs.items():
        tipo = data.get("tipo") or "fijo"
        tipo = "seleccion" if tipo in ("sel", "seleccion", "choice") else "fijo"
        nombre = _combo_group_name(tipo, data.get("nombre"))
        max_sel = max(1, int(data.get("max_selecciones") or 1))
        orden = int(data.get("orden") or 0)
        grp = ComboGroup(
            combo_id=combo.id,
            nombre=nombre,
            tipo=tipo,
            min_selecciones=1 if tipo == "seleccion" else 0,
            max_selecciones=max_sel if tipo == "seleccion" else 1,
            orden=orden,
            requerido=True,
        )
        db.session.add(grp)
        groups_by_uid[uid] = grp

    # ── Fallback: crear grupos "__fixed__" y por nombre-casefold para componentes
    #    sin uid asociado (patrón usado por el socio y como red de seguridad). ──
    fallback_groups: dict = {}

    def _fallback_group_for(comp):
        if comp.es_seleccionable:
            key = f"sel::{(comp.grupo or 'Eleccion').strip().casefold()}"
            if key not in fallback_groups:
                fallback_groups[key] = ComboGroup(
                    combo_id=combo.id,
                    nombre=_combo_group_name("seleccion", comp.grupo),
                    tipo="seleccion",
                    min_selecciones=1,
                    max_selecciones=max(1, int(comp.max_selecciones or 1)),
                    orden=(len(groups_by_uid) + len(fallback_groups)) * 10,
                    requerido=True,
                )
                db.session.add(fallback_groups[key])
        else:
            key = "__fixed__"
            if key not in fallback_groups:
                fallback_groups[key] = ComboGroup(
                    combo_id=combo.id,
                    nombre=_combo_group_name("fijo"),
                    tipo="fijo",
                    min_selecciones=0,
                    max_selecciones=1,
                    orden=0,
                    requerido=True,
                )
                db.session.add(fallback_groups[key])
        return fallback_groups[key]

    db.session.flush()

    created: list = []
    for comp in componentes:
        group = groups_by_uid.get(comp.group_uid) if comp.group_uid else None
        if group is None:
            group = _fallback_group_for(comp)
        db.session.flush()

        new_item = ComboItem(
            combo_id=combo.id,
            combo_group_id=group.id,
            producto_id=comp.producto_id,
            cantidad=comp.cantidad,
            orden=comp.orden,
            activo=True,
            es_seleccionable=comp.es_seleccionable,
            grupo_seleccion=comp.grupo if comp.es_seleccionable else None,
            max_selecciones=comp.max_selecciones if comp.es_seleccionable else 1,
            precio_extra=comp.precio_extra,
            es_predeterminado=comp.es_default if comp.es_seleccionable else False,
            notas_preparacion=comp.nota_preparacion or None,
            presentation_id=comp.presentation_id,
            permite_sabor_cliente=comp.flavor_mode == "cliente_elige",
            fixed_flavor_option_id=comp.fixed_flavor_option_id,
        )
        if comp.allowed_flavor_option_ids:
            new_item.allowed_flavor_options = ProductExtraOption.query.filter(
                ProductExtraOption.id.in_(comp.allowed_flavor_option_ids)
            ).all()
        if len(comp.allowed_presentation_ids or []) >= 2:
            new_item.allowed_presentations = ProductPresentation.query.filter(
                ProductPresentation.id.in_(comp.allowed_presentation_ids)
            ).all()
        db.session.add(new_item)
        created.append(new_item)

    # ── Marcar propuesta pendiente si viene de un socio ──
    if autor_role == "socio_producto":
        combo.partner_submitted_by = actor_user_id
        combo.partner_submitted_at = utcnow()
        combo.partner_submission_status = "pending"
        combo.partner_reviewed_by = None
        combo.partner_reviewed_at = None
        combo.partner_review_note = None
        combo.activo = False

    return created
