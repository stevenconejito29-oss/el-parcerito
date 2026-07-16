"""Geometría de cobertura sin dependencias GIS externas.

El contrato persistido es GeoJSON (lon, lat). Se aceptan Polygon y
MultiPolygon, incluidos huecos, para representar calles o barriadas que no
son alcanzables aunque estén rodeadas por una zona válida.
"""
from __future__ import annotations

import json
import math
from typing import Any


MAX_GEOJSON_BYTES = 100_000
MAX_VERTICES = 1_000
_EPSILON = 1e-10


class CoverageGeometryError(ValueError):
    """Geometría inválida o demasiado compleja para una zona de reparto."""


def _point(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise CoverageGeometryError("Cada punto debe contener longitud y latitud.")
    try:
        lon, lat = float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise CoverageGeometryError("Las coordenadas deben ser numéricas.") from exc
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise CoverageGeometryError("Las coordenadas deben ser finitas.")
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise CoverageGeometryError("Hay coordenadas fuera del rango geográfico válido.")
    return [round(lon, 7), round(lat, 7)]


def _signed_area(ring: list[list[float]]) -> float:
    return sum(
        ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
        for i in range(len(ring) - 1)
    ) / 2


def _orientation(a, b, c) -> int:
    value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
    if abs(value) <= _EPSILON:
        return 0
    return 1 if value > 0 else 2


def _segments_cross(a, b, c, d) -> bool:
    return _orientation(a, b, c) != _orientation(a, b, d) and _orientation(c, d, a) != _orientation(c, d, b)


def _self_intersects(ring: list[list[float]]) -> bool:
    segment_count = len(ring) - 1
    for first in range(segment_count):
        for second in range(first + 1, segment_count):
            if second in (first, first + 1) or (first == 0 and second == segment_count - 1):
                continue
            if _segments_cross(ring[first], ring[first + 1], ring[second], ring[second + 1]):
                return True
    return False


def _ring(value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        raise CoverageGeometryError("Cada contorno debe ser una lista de puntos.")
    points = [_point(point) for point in value]
    if points and points[0] != points[-1]:
        points.append(points[0].copy())
    if len(points) < 4 or len({tuple(point) for point in points[:-1]}) < 3:
        raise CoverageGeometryError("Cada contorno necesita al menos tres puntos distintos.")
    if abs(_signed_area(points)) <= _EPSILON:
        raise CoverageGeometryError("El contorno no puede tener área cero.")
    if _self_intersects(points):
        raise CoverageGeometryError("El contorno se cruza consigo mismo. Reordena los puntos.")
    return points


def normalizar_cobertura_geojson(raw: Any) -> dict | None:
    """Valida y devuelve GeoJSON canónico, o ``None`` si está vacío."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_GEOJSON_BYTES:
            raise CoverageGeometryError("La cobertura supera el tamaño permitido.")
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CoverageGeometryError("La cobertura no contiene GeoJSON válido.") from exc
    if not isinstance(raw, dict):
        raise CoverageGeometryError("La cobertura debe ser un objeto GeoJSON.")

    kind = raw.get("type")
    coordinates = raw.get("coordinates")
    if kind == "Polygon":
        polygons = [coordinates]
    elif kind == "MultiPolygon":
        polygons = coordinates
    else:
        raise CoverageGeometryError("Solo se admiten coberturas Polygon o MultiPolygon.")
    if not isinstance(polygons, list) or not polygons:
        raise CoverageGeometryError("La cobertura debe contener al menos un polígono.")

    normalized = []
    vertices = 0
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise CoverageGeometryError("Cada polígono necesita un contorno exterior.")
        rings = [_ring(ring) for ring in polygon]
        for hole in rings[1:]:
            inside, boundary = _point_in_ring(hole[0][1], hole[0][0], rings[0])
            if not inside or boundary:
                raise CoverageGeometryError("Las áreas excluidas deben quedar dentro del contorno principal.")
        vertices += sum(len(ring) - 1 for ring in rings)
        normalized.append(rings)
    if vertices > MAX_VERTICES:
        raise CoverageGeometryError(
            f"La cobertura supera el máximo de {MAX_VERTICES} vértices. Simplifica el contorno."
        )

    result = {
        "type": kind,
        "coordinates": normalized[0] if kind == "Polygon" else normalized,
    }
    if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > MAX_GEOJSON_BYTES:
        raise CoverageGeometryError("La cobertura supera el tamaño permitido.")
    return result


def _on_segment(lon: float, lat: float, a: list[float], b: list[float]) -> bool:
    cross = (lon - a[0]) * (b[1] - a[1]) - (lat - a[1]) * (b[0] - a[0])
    return (
        abs(cross) <= _EPSILON
        and min(a[0], b[0]) - _EPSILON <= lon <= max(a[0], b[0]) + _EPSILON
        and min(a[1], b[1]) - _EPSILON <= lat <= max(a[1], b[1]) + _EPSILON
    )


def _point_in_ring(lat: float, lon: float, ring: list[list[float]]) -> tuple[bool, bool]:
    inside = False
    for index in range(len(ring) - 1):
        a, b = ring[index], ring[index + 1]
        if _on_segment(lon, lat, a, b):
            return True, True
        if (a[1] > lat) != (b[1] > lat):
            intersection = (b[0] - a[0]) * (lat - a[1]) / (b[1] - a[1]) + a[0]
            if lon < intersection:
                inside = not inside
    return inside, False


def contiene_punto(geometry: dict | str | None, lat: float, lon: float) -> bool:
    """Comprueba un punto. El borde exterior cuenta; el borde de un hueco no."""
    try:
        geometry = normalizar_cobertura_geojson(geometry)
        lat, lon = float(lat), float(lon)
    except (CoverageGeometryError, TypeError, ValueError):
        return False
    if geometry is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    polygons = (
        [geometry["coordinates"]]
        if geometry["type"] == "Polygon"
        else geometry["coordinates"]
    )
    for polygon in polygons:
        outer, _ = _point_in_ring(lat, lon, polygon[0])
        if not outer:
            continue
        excluded = False
        for hole in polygon[1:]:
            in_hole, on_hole = _point_in_ring(lat, lon, hole)
            if in_hole or on_hole:
                excluded = True
                break
        if not excluded:
            return True
    return False


def limites_cobertura(geometry: dict | str | None) -> tuple[float, float, float, float] | None:
    """Devuelve (min_lat, min_lon, max_lat, max_lon)."""
    try:
        geometry = normalizar_cobertura_geojson(geometry)
    except CoverageGeometryError:
        return None
    if geometry is None:
        return None
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    points = [point for polygon in polygons for ring in polygon for point in ring]
    return (
        min(point[1] for point in points),
        min(point[0] for point in points),
        max(point[1] for point in points),
        max(point[0] for point in points),
    )


def contar_vertices(geometry: dict | str | None) -> int:
    try:
        geometry = normalizar_cobertura_geojson(geometry)
    except CoverageGeometryError:
        return 0
    if geometry is None:
        return 0
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return sum(len(ring) - 1 for polygon in polygons for ring in polygon)
