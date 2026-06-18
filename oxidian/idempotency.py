"""Deduplicación de operaciones críticas mediante Idempotency-Key.

Uso típico desde una ruta Flask:

    from idempotency import with_idempotency, request_idempotency_key, request_body_hash

    key = request_idempotency_key("checkout_web", auto_seed=str(current_user.id))
    body_hash = request_body_hash()

    def crear():
        # ... lógica que crea el Order ...
        return 200, {"order_id": pedido.id, "numero": pedido.numero_pedido}, pedido.id

    status, body, replayed = with_idempotency("checkout_web", key, body_hash, crear,
                                              user_id=current_user.id)
    return jsonify(body), status

Garantías:
- Una key + scope solo se procesa una vez. Reintentos con el mismo body devuelven
  la respuesta cacheada (replayed=True).
- Si llega una key duplicada con un body distinto, devuelve 409 inmediato — el
  cliente está reusando un nonce, posible bug o ataque.
- Las claves caducan a las 24 h. Cleanup periódico opcional vía
  `purge_expired_idempotency_keys()`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from datetime import timedelta
from typing import Callable, Tuple

from flask import request
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import IdempotencyKey, utcnow

logger = logging.getLogger(__name__)

IDEMPOTENCY_TTL = timedelta(hours=24)
# Si el cliente no envía Idempotency-Key, agrupamos POSTs idénticos hechos por
# la misma fuente en esta ventana (segundos). Es defensa contra double-click,
# no contra retries auténticos.
AUTO_KEY_WINDOW_SECONDS = 30


def request_body_hash() -> str:
    """SHA-256 hex del cuerpo crudo de la petición (form o JSON normalizado).

    Para form encoded ordena las claves y serializa como JSON: así un mismo
    formulario produce siempre el mismo hash aunque el navegador cambie el
    orden de los campos.
    """
    raw = request.get_data(cache=True, as_text=False) or b""
    h = hashlib.sha256(raw)
    return h.hexdigest()


def request_idempotency_key(scope: str, auto_seed: str | None = None) -> str:
    """Devuelve la Idempotency-Key del header, o una clave automática derivada
    de (scope + seed + body_hash + ventana temporal).

    `auto_seed` puede ser user_id, IP, csrf_token, etc. Si el cliente sí envía
    el header, este parámetro se ignora.
    """
    explicit = (request.headers.get("Idempotency-Key") or "").strip()
    if explicit:
        # Limitamos a 120 chars para que quepa en la columna.
        return explicit[:120]
    bucket = int(time.time() // AUTO_KEY_WINDOW_SECONDS)
    raw = f"{scope}|{auto_seed or ''}|{request_body_hash()}|{bucket}"
    return "auto:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def with_idempotency(
    scope: str,
    key: str,
    body_hash: str,
    fn: Callable[[], Tuple[int, dict, int | None]],
    user_id: int | None = None,
) -> Tuple[int, dict, bool]:
    """Ejecuta `fn` solo la primera vez para (scope, key); reintentos devuelven
    la respuesta cacheada.

    `fn` debe devolver `(status, body_dict, order_id_or_none)`.

    Retorna `(status, body, replayed)`:
    - `replayed=False` significa que `fn` se ejecutó ahora.
    - `replayed=True` significa que se sirvió desde caché.
    - `status=409` con body `{"error": "idempotency_key_conflict"}` si la
      key ya existe con otro body_hash.
    """
    if not key:
        raise ValueError("Idempotency key requerida")

    existente = IdempotencyKey.query.filter_by(scope=scope, key=key).first()
    if existente:
        if existente.request_hash != body_hash:
            logger.warning(
                "Idempotency conflict scope=%s key=%s (hash mismatch)",
                scope, key[:16],
            )
            return (
                409,
                {"error": "idempotency_key_conflict",
                 "message": "La misma key se reutilizó con un cuerpo distinto."},
                True,
            )
        # Hit: devolver respuesta congelada
        try:
            cached = json.loads(existente.response_body or "{}")
        except (json.JSONDecodeError, TypeError):
            cached = {}
        return (existente.response_status, cached, True)

    # Miss: ejecutamos. Si dos procesos llegan simultáneamente al miss, el
    # UNIQUE(scope, key) bloqueará al segundo en la inserción.
    status, body, order_id = fn()

    entry = IdempotencyKey(
        scope=scope,
        key=key,
        request_hash=body_hash,
        response_status=status,
        response_body=json.dumps(body, ensure_ascii=False, default=str)[:1_000_000],
        order_id=order_id,
        user_id=user_id,
        expira_en=utcnow() + IDEMPOTENCY_TTL,
    )
    db.session.add(entry)
    try:
        db.session.flush()
    except IntegrityError:
        # Carrera: otro proceso insertó la misma key un microsegundo antes.
        # Cargamos su respuesta y devolvemos.
        db.session.rollback()
        ganador = IdempotencyKey.query.filter_by(scope=scope, key=key).first()
        if not ganador:
            raise
        if ganador.request_hash != body_hash:
            return (
                409,
                {"error": "idempotency_key_conflict",
                 "message": "Race condition con body distinto."},
                True,
            )
        try:
            cached = json.loads(ganador.response_body or "{}")
        except (json.JSONDecodeError, TypeError):
            cached = {}
        return (ganador.response_status, cached, True)

    return (status, body, False)


def purge_expired_idempotency_keys(batch_size: int = 5000) -> int:
    """Borra entradas caducadas. Devuelve cuántas se eliminaron."""
    ahora = utcnow()
    rows = (
        IdempotencyKey.query
        .filter(IdempotencyKey.expira_en < ahora)
        .limit(batch_size)
        .all()
    )
    for row in rows:
        db.session.delete(row)
    db.session.commit()
    return len(rows)


def new_client_key() -> str:
    """Utility para que el bot/POS generen una key fresh tipo UUID."""
    return secrets.token_urlsafe(24)
