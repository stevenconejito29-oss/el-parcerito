"""Autorización de pedidos por sesión firmada; nunca publica sus credenciales."""
import secrets
from datetime import datetime, timezone
from flask import session


def visitor_order_tokens():
    raw = session.get('guest_order_tokens', {})
    if not isinstance(raw, dict):
        return {}
    now = int(datetime.now(timezone.utc).timestamp())
    result = {}
    for key, value in raw.items():
        if not str(key).isdigit():
            continue
        if isinstance(value, dict):
            try:
                expiry = int(value.get('exp') or 0)
            except (TypeError, ValueError):
                continue
            if expiry and expiry < now:
                continue
            value = value.get('token')
        if isinstance(value, str) and value.strip():
            result[int(key)] = value
    return result


def session_authorizes_order(order_id, supplied_token=''):
    expected = visitor_order_tokens().get(order_id)
    # La credencial antigua solo se admite junto a la misma sesión propietaria.
    return bool(expected and (not supplied_token or secrets.compare_digest(expected, supplied_token)))
