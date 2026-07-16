(function () {
  'use strict';

  document.querySelectorAll('[data-zone-coverage-editor]').forEach(function (root) {
    if (typeof window.L === 'undefined') return;
    const mapNode = root.querySelector('[data-zone-map]');
    const field = root.querySelector('[name="cobertura_geojson"]');
    const status = root.querySelector('[data-zone-status]');
    if (!mapNode || !field) return;

    const center = [Number(mapNode.dataset.centerLat), Number(mapNode.dataset.centerLng)];
    const map = L.map(mapNode, { center: center, zoom: 14, scrollWheelZoom: true });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);

    let points = [];
    let geometryLayer = null;
    let draftLayer = null;
    let markers = [];

    function setStatus(text, tone) {
      if (!status) return;
      status.textContent = text;
      status.dataset.tone = tone || 'neutral';
    }

    function removeLayer(layer) {
      if (layer) map.removeLayer(layer);
    }

    function clearMarkers() {
      markers.forEach(function (marker) { map.removeLayer(marker); });
      markers = [];
    }

    function geometryFromPoints() {
      if (points.length < 3) return '';
      const ring = points.map(function (point) { return [point.lng, point.lat]; });
      ring.push(ring[0].slice());
      return JSON.stringify({ type: 'Polygon', coordinates: [ring] });
    }

    function renderDraft() {
      removeLayer(draftLayer);
      clearMarkers();
      points.forEach(function (point, index) {
        markers.push(L.circleMarker(point, {
          radius: 5, color: '#172554', fillColor: '#facc15', fillOpacity: 1, weight: 2
        }).addTo(map).bindTooltip(String(index + 1)));
      });
      if (points.length >= 2) {
        draftLayer = points.length >= 3
          ? L.polygon(points, { color: '#eab308', fillOpacity: 0.22, weight: 3 }).addTo(map)
          : L.polyline(points, { color: '#eab308', weight: 3 }).addTo(map);
      }
      field.value = geometryFromPoints();
      if (points.length < 3) {
        setStatus(points.length ? 'Añade ' + (3 - points.length) + ' punto(s) más.' : 'Pulsa el mapa para comenzar.', 'neutral');
      } else {
        setStatus('Cobertura lista: ' + points.length + ' vértices. Guarda los cambios para aplicarla.', 'success');
      }
    }

    function loadExisting() {
      if (!field.value.trim()) return;
      try {
        const geometry = JSON.parse(field.value);
        geometryLayer = L.geoJSON(geometry, {
          style: { color: '#16a34a', fillColor: '#facc15', fillOpacity: 0.2, weight: 3 }
        }).addTo(map);
        const bounds = geometryLayer.getBounds();
        if (bounds.isValid()) map.fitBounds(bounds, { padding: [24, 24] });
        setStatus('Cobertura detallada activa. Pulsa el mapa para sustituirla.', 'success');
      } catch (_error) {
        field.value = '';
        setStatus('La cobertura anterior no era válida; dibuja una nueva.', 'error');
      }
    }

    map.on('click', function (event) {
      if (geometryLayer) {
        removeLayer(geometryLayer);
        geometryLayer = null;
        points = [];
      }
      points.push(event.latlng);
      renderDraft();
    });

    root.querySelector('[data-zone-undo]')?.addEventListener('click', function () {
      if (geometryLayer) return;
      points.pop();
      renderDraft();
    });
    root.querySelector('[data-zone-clear]')?.addEventListener('click', function () {
      removeLayer(geometryLayer);
      removeLayer(draftLayer);
      geometryLayer = null;
      draftLayer = null;
      points = [];
      clearMarkers();
      field.value = '';
      setStatus('Cobertura detallada eliminada. Se usará el radio compatible si está completo.', 'neutral');
    });
    root.querySelector('[data-zone-locate]')?.addEventListener('click', function () {
      if (!navigator.geolocation) {
        setStatus('Este dispositivo no ofrece geolocalización.', 'error');
        return;
      }
      navigator.geolocation.getCurrentPosition(function (position) {
        map.setView([position.coords.latitude, position.coords.longitude], 16);
        setStatus('Mapa centrado en tu ubicación. Ahora marca el contorno.', 'neutral');
      }, function () {
        setStatus('No fue posible obtener tu ubicación.', 'error');
      }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 });
    });
    loadExisting();
    window.setTimeout(function () { map.invalidateSize(); }, 0);
  });
})();
