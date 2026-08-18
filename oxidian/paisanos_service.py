"""Portal /paisanos — refresco de datos externos y snapshot para la vista.

Widgets en cabecera (COP/EUR, USD/COP, clima Carmona/Bogotá) + noticias de
Colombia (RSS El Tiempo, Semana, El Espectador) + BOE filtrado por keywords
de extranjería/residencia. Todo se persiste en Redis y la vista sólo lee.

Política de fallos: si una fuente externa cae, mantenemos la snapshot
anterior en Redis (no la borramos). Así el widget nunca queda vacío por un
timeout puntual — sólo aparece el placeholder "actualizando…" si nunca ha
respondido.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger("paisanos")

# ── claves Redis públicas ────────────────────────────────────────────────
REDIS_KEY_TASAS = "paisanos:tasas"
REDIS_KEY_CLIMA = "paisanos:clima"
REDIS_KEY_NOTICIAS_CO = "paisanos:noticias_co"
REDIS_KEY_TRAMITES_BOE = "paisanos:tramites_boe"

_HTTP_TIMEOUT = 8
_HTTP_HEADERS = {
    "User-Agent": "ElParcerito/Paisanos (+https://elparcerito.com)",
    "Accept": "application/json, text/xml, application/rss+xml, */*",
}

# Palabras clave para filtrar avisos del BOE relevantes para colombianos y
# extranjeros en general. Coincidencia por substring case-insensitive sobre
# título+descripción del ítem RSS.
_BOE_KEYWORDS = (
    "extranjería", "extranjeria",
    "extranjeros", "extranjero",
    "residencia",
    "arraigo",
    "nacionalidad",
    "asilo",
)


# ── cliente Redis (lazy, singleton por proceso) ──────────────────────────
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    url = os.environ.get("REDIS_URL", "")
    if not url.startswith(("redis://", "rediss://")):
        return None
    try:
        import redis  # type: ignore
        _redis_client = redis.Redis.from_url(
            url, socket_connect_timeout=1.5, socket_timeout=1.5, decode_responses=True,
        )
        _redis_client.ping()
    except Exception as exc:  # pragma: no cover
        log.warning("paisanos: Redis no disponible (%s)", exc)
        _redis_client = None
    return _redis_client


def _redis_set_json(key: str, value: Any) -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        r.set(key, json.dumps(value, ensure_ascii=False))
    except Exception:
        log.exception("paisanos: fallo al escribir %s en Redis", key)


def _redis_get_json(key: str):
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        log.exception("paisanos: fallo al leer %s de Redis", key)
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── refresco de tasas COP ────────────────────────────────────────────────
def refrescar_tasas() -> dict | None:
    """COP/EUR y USD/COP. Fuente: open.er-api.com (gratis, sin API key)."""
    result = {"cop_eur": None, "usd_cop": None, "updated_at": _now_iso()}
    try:
        r1 = requests.get(
            "https://open.er-api.com/v6/latest/EUR",
            timeout=_HTTP_TIMEOUT, headers=_HTTP_HEADERS,
        )
        r1.raise_for_status()
        data1 = r1.json() or {}
        if data1.get("result") == "success":
            result["cop_eur"] = (data1.get("rates") or {}).get("COP")
    except Exception as exc:
        log.warning("paisanos: fallo COP/EUR: %s", exc)

    try:
        r2 = requests.get(
            "https://open.er-api.com/v6/latest/USD",
            timeout=_HTTP_TIMEOUT, headers=_HTTP_HEADERS,
        )
        r2.raise_for_status()
        data2 = r2.json() or {}
        if data2.get("result") == "success":
            result["usd_cop"] = (data2.get("rates") or {}).get("COP")
    except Exception as exc:
        log.warning("paisanos: fallo USD/COP: %s", exc)

    if result["cop_eur"] is None and result["usd_cop"] is None:
        # Fallo total: conservamos snapshot previo si existe.
        previo = _redis_get_json(REDIS_KEY_TASAS)
        if previo:
            log.info("paisanos: mantenemos tasas cacheadas (todas las APIs fallaron)")
            return previo
    _redis_set_json(REDIS_KEY_TASAS, result)
    return result


# ── refresco de clima ────────────────────────────────────────────────────
def _open_meteo(lat: float, lon: float) -> dict | None:
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current_weather": "true"},
            timeout=_HTTP_TIMEOUT, headers=_HTTP_HEADERS,
        )
        r.raise_for_status()
        data = r.json() or {}
        cw = data.get("current_weather") or {}
        if not cw:
            return None
        return {
            "temperatura": cw.get("temperature"),
            "viento": cw.get("windspeed"),
            "codigo": cw.get("weathercode"),
            "hora": cw.get("time"),
        }
    except Exception as exc:
        log.warning("paisanos: fallo clima (%s,%s): %s", lat, lon, exc)
        return None


def refrescar_clima() -> dict | None:
    carmona = _open_meteo(37.4691, -5.6420)
    bogota = _open_meteo(4.7110, -74.0721)
    if carmona is None and bogota is None:
        previo = _redis_get_json(REDIS_KEY_CLIMA)
        if previo:
            log.info("paisanos: mantenemos clima cacheado (Open-Meteo caído)")
            return previo
    payload = {
        "carmona": carmona,
        "bogota": bogota,
        "updated_at": _now_iso(),
    }
    _redis_set_json(REDIS_KEY_CLIMA, payload)
    return payload


# ── parseo RSS con feedparser ────────────────────────────────────────────
def _parse_feed(url: str, limite: int = 8) -> list[dict]:
    try:
        import feedparser  # type: ignore
    except ImportError:
        log.error("paisanos: feedparser no está instalado")
        return []
    try:
        # feedparser sabe hacer GET, pero pasamos por requests para
        # controlar timeout y UA de forma uniforme.
        resp = requests.get(url, timeout=_HTTP_TIMEOUT, headers=_HTTP_HEADERS)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as exc:
        log.warning("paisanos: fallo RSS %s: %s", url, exc)
        return []

    items = []
    for entry in (parsed.entries or [])[:limite]:
        items.append({
            "titulo": (entry.get("title") or "").strip(),
            "enlace": (entry.get("link") or "").strip(),
            "fuente": (parsed.feed.get("title") if parsed.feed else "") or url,
            "publicado": entry.get("published", "") or entry.get("updated", ""),
            "resumen": (entry.get("summary") or "").strip()[:280],
        })
    return items


_FEEDS_NOTICIAS_CO = [
    "https://www.eltiempo.com/rss/colombia.xml",
    "https://news.google.com/rss/search?q=site:semana.com&hl=es-CO&gl=CO&ceid=CO:es-419",
    "https://news.google.com/rss/search?q=site:elespectador.com&hl=es-CO&gl=CO&ceid=CO:es-419",
]


def refrescar_noticias_co() -> list[dict] | None:
    todos: list[dict] = []
    for url in _FEEDS_NOTICIAS_CO:
        todos.extend(_parse_feed(url, limite=4))
    # Intercalado justo entre fuentes ya viene dado por el orden de _parse_feed.
    # Cortamos a 8 titulares max para la vista.
    if not todos:
        previo = _redis_get_json(REDIS_KEY_NOTICIAS_CO)
        if previo:
            log.info("paisanos: mantenemos noticias CO cacheadas (todas las fuentes caídas)")
            return previo
    payload = {"items": todos[:8], "updated_at": _now_iso()}
    _redis_set_json(REDIS_KEY_NOTICIAS_CO, payload)
    return payload


# ── BOE ──────────────────────────────────────────────────────────────────
def _relevante_boe(item: dict) -> bool:
    haystack = " ".join([
        (item.get("titulo") or "").lower(),
        (item.get("resumen") or "").lower(),
    ])
    return any(kw in haystack for kw in _BOE_KEYWORDS)


_FEEDS_TRAMITES = [
    "https://news.google.com/rss/search?q=NIE+extranjer%C3%ADa+colombianos+Espa%C3%B1a&hl=es-CO&gl=CO&ceid=CO:es-419",
    "https://news.google.com/rss/search?q=arraigo+residencia+colombianos+Espa%C3%B1a&hl=es-CO&gl=CO&ceid=CO:es-419",
    "https://news.google.com/rss/search?q=homologaci%C3%B3n+t%C3%ADtulos+Colombia+Espa%C3%B1a&hl=es-CO&gl=CO&ceid=CO:es-419",
]


def refrescar_tramites_boe() -> list[dict] | None:
    """Trámites de extranjería relevantes para colombianos.

    El feed oficial BOE cambió y ya no sirve por RSS estándar; nos apoyamos
    en Google News con queries específicas sobre NIE, arraigo y homologación
    de títulos.
    """
    todos: list[dict] = []
    for url in _FEEDS_TRAMITES:
        todos.extend(_parse_feed(url, limite=3))
    if not todos:
        previo = _redis_get_json(REDIS_KEY_TRAMITES_BOE)
        if previo:
            log.info("paisanos: mantenemos trámites cacheados (fuentes caídas)")
            return previo
    payload = {"items": todos[:8], "updated_at": _now_iso()}
    _redis_set_json(REDIS_KEY_TRAMITES_BOE, payload)
    return payload


# ── orquestación ────────────────────────────────────────────────────────
def refrescar_todo() -> None:
    """Punto único invocable por APScheduler y por el arranque."""
    inicio = time.time()
    for fn in (refrescar_tasas, refrescar_clima,
               refrescar_noticias_co, refrescar_tramites_boe):
        try:
            fn()
        except Exception:
            log.exception("paisanos: excepción en %s", fn.__name__)
    log.info("paisanos: refresco completo en %.2fs", time.time() - inicio)


def snapshot_para_vista() -> dict:
    """Lee todo lo que hay en Redis para pintar /paisanos.
    Si algo falta devolvemos None en esa sección → la vista pinta placeholder.
    """
    return {
        "tasas": _redis_get_json(REDIS_KEY_TASAS),
        "clima": _redis_get_json(REDIS_KEY_CLIMA),
        "noticias_co": _redis_get_json(REDIS_KEY_NOTICIAS_CO),
        "tramites_boe": _redis_get_json(REDIS_KEY_TRAMITES_BOE),
    }
