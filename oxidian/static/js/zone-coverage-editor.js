(function () {
  'use strict';

  document.querySelectorAll('[data-zone-coverage-editor]').forEach(function (root) {
    if (typeof window.L === 'undefined') return;
    const mapNode = root.querySelector('[data-zone-map]');
    const field = root.querySelector('[name="cobertura_geojson"]');
    const status = root.querySelector('[data-zone-status]');
    const submitButton = root.querySelector('[data-zone-submit]');
    const geometryRequired = root.dataset.requireGeometry === 'true';
    if (!mapNode || !field) return;

    const center = [Number(mapNode.dataset.centerLat), Number(mapNode.dataset.centerLng)];
    const map = L.map(mapNode, { center: center, zoom: 14, scrollWheelZoom: true });
    map.doubleClickZoom.disable();
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

    function updateSubmitState(valid) {
      if (submitButton && geometryRequired) {
        submitButton.disabled = !valid;
        submitButton.setAttribute('aria-disabled', valid ? 'false' : 'true');
      }
    }

    function renderDraft() {
      removeLayer(draftLayer);
      clearMarkers();
      points.forEach(function (point, index) {
        const marker = L.marker(point, {
          draggable: true,
          icon: L.divIcon({
            className: 'zone-coverage-vertex',
            html: String(index + 1),
            iconSize: [26, 26],
            iconAnchor: [13, 13]
          })
        }).addTo(map);
        marker.on('dragend', function (event) {
          points[index] = event.target.getLatLng();
          renderDraft();
        });
        markers.push(marker);
      });
      if (points.length >= 2) {
        draftLayer = points.length >= 3
          ? L.polygon(points, { color: '#eab308', fillOpacity: 0.22, weight: 3 }).addTo(map)
          : L.polyline(points, { color: '#eab308', weight: 3 }).addTo(map);
      }
      field.value = geometryFromPoints();
      updateSubmitState(points.length >= 3);
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
        updateSubmitState(true);
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
      updateSubmitState(false);
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
    if (!field.value.trim()) updateSubmitState(false);
    if (geometryRequired) {
      root.addEventListener('submit', function (event) {
        if (!field.value.trim()) {
          event.preventDefault();
          setStatus('Marca al menos tres puntos en el mapa antes de guardar.', 'error');
          mapNode.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      });
    }
    const disclosure = root.closest('details');
    disclosure?.addEventListener('toggle', function () {
      if (disclosure.open) window.setTimeout(function () { map.invalidateSize(); }, 0);
    });
    window.setTimeout(function () { map.invalidateSize(); }, 0);
  });
})();
