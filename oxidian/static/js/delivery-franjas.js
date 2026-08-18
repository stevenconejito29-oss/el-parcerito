/* ═══════════════════════════════════════════════════════════════
   Módulo delivery por franjas — helpers de UI compartidos.
   Cero dependencias externas. Usa fetch + CSRF del meta tag.
   ═══════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  const CSRF = document.querySelector('meta[name="ox-csrf-token"]')?.content || "";

  const DIAS = ["Domingo","Lunes","Martes","Miércoles","Jueves","Viernes","Sábado"];
  const MESES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"];

  function fmtFecha(iso) {
    const d = new Date(iso + "T00:00:00");
    // Prefijo relativo — "Hoy"/"Mañana" ayudan al cliente a ubicarse rápido
    // en el selector; el resto de días muestran nombre de la semana.
    const hoy = new Date(); hoy.setHours(0,0,0,0);
    const diffDias = Math.round((d - hoy) / 86400000);
    if (diffDias === 0) return `Hoy · ${d.getDate()} ${MESES[d.getMonth()]}`;
    if (diffDias === 1) return `Mañana · ${d.getDate()} ${MESES[d.getMonth()]}`;
    return `${DIAS[d.getDay()]} ${d.getDate()} ${MESES[d.getMonth()]}`;
  }

  // Chip con el cupo restante — tono según saturación. Compartido por el
  // selector cliente y el panel del repartidor.
  function cupoChip(libre, total) {
    if (libre <= 0) return `<span class="df-cupo-chip is-nulo">Sin cupo</span>`;
    const bajo = total > 0 && libre / total <= 0.34;
    const cls = bajo ? "is-bajo" : "";
    const txt = libre === 1 ? "1 hueco libre" : `${libre} huecos libres`;
    return `<span class="df-cupo-chip ${cls}">${txt}</span>`;
  }

  function agruparPorFecha(franjas) {
    const map = new Map();
    franjas.forEach(f => {
      if (!map.has(f.fecha)) map.set(f.fecha, []);
      map.get(f.fecha).push(f);
    });
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }

  async function apiFetch(url, opts = {}) {
    const headers = { "Accept": "application/json", ...(opts.headers || {}) };
    if (opts.body && typeof opts.body === "object") {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    if (opts.method && opts.method !== "GET") headers["X-CSRFToken"] = CSRF;
    const res = await fetch(url, { ...opts, headers, credentials: "same-origin" });
    let payload = null;
    try { payload = await res.json(); } catch (_) {}
    return { ok: res.ok, status: res.status, data: payload };
  }

  function msg(container, text, type) {
    const el = document.createElement("div");
    el.className = `df-msg df-msg-${type || "info"}`;
    el.textContent = text;
    container.prepend(el);
    setTimeout(() => el.remove(), 5000);
  }

  window.DeliveryFranjas = { fmtFecha, agruparPorFecha, apiFetch, msg, cupoChip };
})();
