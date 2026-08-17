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
    return `${DIAS[d.getDay()]} ${d.getDate()} ${MESES[d.getMonth()]}`;
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

  window.DeliveryFranjas = { fmtFecha, agruparPorFecha, apiFetch, msg };
})();
