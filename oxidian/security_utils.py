"""Primitivas pequeñas de seguridad HTTP compartidas por los blueprints."""
from urllib.parse import urlsplit

from flask import request


def is_safe_local_path(value: str) -> bool:
    """Acepta destinos internos sin normalizaciones ambiguas del navegador."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return False
    if "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return not parsed.scheme and not parsed.netloc


def safe_local_referrer(fallback: str, allowed_prefixes: tuple[str, ...] = ()) -> str:
    """Devuelve un Referer local como ruta o un destino interno seguro.

    Nunca devuelve esquema/host aportado por el cliente. ``allowed_prefixes``
    permite limitar además la sección a la que puede regresar una operación.
    """
    value = request.referrer or ""
    if not value or "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return fallback
    try:
        parsed = urlsplit(value)
    except ValueError:
        return fallback
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return fallback
    if parsed.netloc and parsed.netloc.casefold() != request.host.casefold():
        return fallback
    path = parsed.path or "/"
    if not is_safe_local_path(path):
        return fallback
    if allowed_prefixes and not any(path.startswith(prefix) for prefix in allowed_prefixes):
        return fallback
    return path + (f"?{parsed.query}" if parsed.query else "")
