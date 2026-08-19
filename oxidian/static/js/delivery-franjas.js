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

  // ═══════════════════════════════════════════════════════════════
  // Calendario semanal visual (7 columnas) — reutilizable.
  // Uso: renderCalendarioSemanal(container, franjas, {modo, semanaInicio, onSlotClick, onEmptyDayClick})
  // ═══════════════════════════════════════════════════════════════

  const DIAS_CORTOS = ["DOM", "LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB"];
  const DIAS_LARGOS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
  const MESES_LARGOS = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"];

  function toISO(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }
  function parseISO(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d);
  }
  function startOfDay(d) { const x = new Date(d); x.setHours(0,0,0,0); return x; }
  function addDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }

  // Semana ISO: lunes → domingo. Devuelve [lunes, domingo].
  function weekRange(date) {
    const d = startOfDay(date);
    const dow = d.getDay();
    const desplazA_Lun = dow === 0 ? -6 : 1 - dow;
    const lunes = addDays(d, desplazA_Lun);
    const domingo = addDays(lunes, 6);
    return [lunes, domingo];
  }

  function slotEstado(s) {
    if (s.cerrada) return "cerrada";
    if (s.llena || (s.capacidad_max != null && s.ocupados >= s.capacidad_max)) return "lleno";
    const libre = (s.capacidad_max || 0) - (s.ocupados || 0);
    if (s.capacidad_max > 0 && libre / s.capacidad_max <= 0.5) return "medio";
    return "libre";
  }

  function fmtRangoSemana(lunes, domingo) {
    const mismoMes = lunes.getMonth() === domingo.getMonth();
    const y = domingo.getFullYear();
    if (mismoMes) {
      return `Semana del ${lunes.getDate()} al ${domingo.getDate()} de ${MESES_LARGOS[lunes.getMonth()].slice(0,3)} ${y}`;
    }
    return `Semana del ${lunes.getDate()} ${MESES_LARGOS[lunes.getMonth()].slice(0,3)} al ${domingo.getDate()} ${MESES_LARGOS[domingo.getMonth()].slice(0,3)} ${y}`;
  }

  function navegarSemana(delta, semanaActual) {
    const nueva = addDays(semanaActual, delta * 7);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("semana", toISO(nueva));
      window.history.replaceState({}, "", url.toString());
    } catch (_) { /* silent */ }
    return nueva;
  }

  function _fechaLarga(iso) {
    const d = parseISO(iso);
    return `${DIAS_LARGOS[d.getDay()]} ${d.getDate()} de ${MESES_LARGOS[d.getMonth()]}`;
  }

  function _slotBlockHTML(s, opts) {
    const estado = slotEstado(s);
    const clases = ["df-cal-slot", `df-cal-slot--${estado}`];
    if (s.sugerida && opts.modo === "cliente") clases.push("df-cal-slot--sugerida");
    if (opts.modo === "admin" && s.activo === false) clases.push("df-cal-slot--inactivo");
    const libre = (s.capacidad_max || 0) - (s.ocupados || 0);
    let chip;
    if (estado === "cerrada") chip = "🔒 Cerrada";
    else if (estado === "lleno") chip = "LLENO";
    else chip = `${libre}/${s.capacidad_max}`;
    const fechaLarga = _fechaLarga(s.fecha);
    const detalle = estado === "cerrada" ? "cerrada"
                  : estado === "lleno"  ? "sin huecos"
                  : `${libre} de ${s.capacidad_max} huecos libres`;
    const aria = `Franja ${fechaLarga}, ${s.hora_inicio} a ${s.hora_fin}, ${detalle}${s.sugerida ? ", sugerida" : ""}`;
    const bloqueado = (estado === "lleno" || estado === "cerrada") && opts.modo === "cliente";
    return `<div class="${clases.join(' ')}" role="button"
        tabindex="${bloqueado ? -1 : 0}"
        aria-disabled="${bloqueado}"
        aria-label="${aria}"
        data-slot-id="${s.id}"
        data-fecha="${s.fecha}"
        data-hora-inicio="${s.hora_inicio}"
        data-hora-fin="${s.hora_fin}"
        data-capacidad="${s.capacidad_max}"
        data-max-repartidores="${s.max_repartidores || ''}"
        data-cierre-modo="${s.cierre_modo || ''}"
        data-cierre-valor="${s.cierre_valor || ''}"
        data-activo="${s.activo === false ? '0' : '1'}"
        data-label="${DIAS_CORTOS[parseISO(s.fecha).getDay()]} ${parseISO(s.fecha).getDate()} · ${s.hora_inicio}–${s.hora_fin}"
        data-estado="${estado}">
      <span class="df-cal-slot__hora">${s.hora_inicio} · ${s.hora_fin}</span>
      <span class="df-cal-slot__chip">${chip}</span>
    </div>`;
  }

  function renderCalendarioSemanal(container, franjas, opts) {
    opts = Object.assign({
      modo: "cliente",
      semanaInicio: null,
      onSlotClick: null,
      onEmptyDayClick: null,
      activeDay: null,
    }, opts || {});
    const [lunes] = weekRange(opts.semanaInicio || new Date());
    const hoy = startOfDay(new Date());
    const porFecha = new Map();
    (franjas || []).forEach(f => {
      if (!porFecha.has(f.fecha)) porFecha.set(f.fecha, []);
      porFecha.get(f.fecha).push(f);
    });
    porFecha.forEach(arr => arr.sort((a, b) => a.hora_inicio.localeCompare(b.hora_inicio)));

    let totalFranjas = 0;
    const days = [];
    for (let i = 0; i < 7; i++) {
      const d = addDays(lunes, i);
      const iso = toISO(d);
      const slots = porFecha.get(iso) || [];
      totalFranjas += slots.length;
      const esHoy = d.getTime() === hoy.getTime();
      const esPasado = d < hoy;
      const activoTab = opts.activeDay ? opts.activeDay === iso : (esHoy || (i === 0 && lunes >= hoy));
      days.push({ d, iso, slots, esHoy, esPasado, activo: activoTab });
    }
    if (!days.some(x => x.activo)) days[0].activo = true;

    if (totalFranjas === 0 && opts.modo === "cliente") {
      container.innerHTML = `<div class="df-cal-empty-week">
        <div class="df-cal-empty-week__icon" aria-hidden="true">📅</div>
        <div class="df-cal-empty-week__title">No hay franjas disponibles esta semana</div>
        <div>Prueba con otra semana o contacta con la tienda.</div>
      </div>`;
      return;
    }

    const tabsHTML = days.map(x => {
      const clases = ["df-cal-day-tab"];
      if (x.activo) clases.push("is-active");
      if (x.esHoy) clases.push("is-today");
      if (x.esPasado) clases.push("is-past");
      return `<button type="button" class="${clases.join(' ')}" role="tab"
        aria-selected="${x.activo ? 'true' : 'false'}"
        data-day-tab="${x.iso}"
        aria-label="${DIAS_LARGOS[x.d.getDay()]} ${x.d.getDate()} de ${MESES_LARGOS[x.d.getMonth()]}${x.esHoy ? ' — hoy' : ''}">
        <span>${DIAS_CORTOS[x.d.getDay()]}</span>
        <strong>${x.d.getDate()}</strong>
      </button>`;
    }).join("");

    const gridHTML = days.map(x => {
      const clases = ["df-cal-day"];
      if (x.esHoy) clases.push("is-today");
      if (x.esPasado) clases.push("is-past");
      if (x.activo) clases.push("is-active");
      const slotsHTML = x.slots.length
        ? x.slots.map(s => _slotBlockHTML(s, opts)).join("")
        : (opts.modo === "admin"
            ? `<div class="df-cal-day-empty">Sin franjas</div>`
            : `<div class="df-cal-day-empty">Sin cupos</div>`);
      const addBtn = (opts.modo === "admin" && !x.esPasado)
        ? `<button type="button" class="df-cal-day-add" data-add-day="${x.iso}"
             aria-label="Añadir franja en ${DIAS_LARGOS[x.d.getDay()]} ${x.d.getDate()}">+ Añadir</button>`
        : "";
      // Botón toggle día: sólo admin, con al menos una franja. Si todas
      // están inactivas ofrece "Activar día"; si alguna activa, "Desactivar día".
      let toggleBtn = "";
      if (opts.modo === "admin" && x.slots.length > 0) {
        const algunaActiva = x.slots.some(s => s.activo !== false);
        const label = algunaActiva ? "⏸ Desactivar día" : "▶ Activar día";
        const accion = algunaActiva ? "0" : "1";
        toggleBtn = `<button type="button" class="df-cal-day-toggle"
             data-toggle-day="${x.iso}" data-activar="${accion}"
             aria-label="${label} ${DIAS_LARGOS[x.d.getDay()]} ${x.d.getDate()}">${label}</button>`;
      }
      return `<div class="${clases.join(' ')}" data-day="${x.iso}" role="gridcell">
        <div class="df-cal-day-header">
          <span class="df-cal-day-header__weekday">${DIAS_CORTOS[x.d.getDay()]}</span>
          <span class="df-cal-day-header__day">${x.d.getDate()}</span>
          ${x.esHoy ? '<span class="df-cal-day-header__today-badge">HOY</span>' : ''}
          ${toggleBtn}
        </div>
        <div class="df-cal-day-body">${slotsHTML}</div>
        ${addBtn}
      </div>`;
    }).join("");

    container.innerHTML = `
      <div class="df-cal-day-tabs" role="tablist" aria-label="Días de la semana">${tabsHTML}</div>
      <div class="df-calendar" role="grid" aria-label="Calendario semanal de franjas">${gridHTML}</div>
    `;

    const tabs = container.querySelectorAll("[data-day-tab]");
    const dayCells = container.querySelectorAll(".df-cal-day");
    tabs.forEach(t => t.addEventListener("click", () => {
      const iso = t.dataset.dayTab;
      tabs.forEach(x => {
        const on = x === t;
        x.classList.toggle("is-active", on);
        x.setAttribute("aria-selected", on ? "true" : "false");
      });
      dayCells.forEach(c => c.classList.toggle("is-active", c.dataset.day === iso));
    }));
    container.querySelectorAll(".df-cal-slot").forEach(el => {
      const activar = () => {
        if (el.getAttribute("aria-disabled") === "true") return;
        if (typeof opts.onSlotClick === "function") opts.onSlotClick(el);
      };
      el.addEventListener("click", activar);
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activar(); }
      });
    });
    container.querySelectorAll("[data-add-day]").forEach(btn => {
      btn.addEventListener("click", () => {
        if (typeof opts.onEmptyDayClick === "function") opts.onEmptyDayClick(btn.dataset.addDay);
      });
    });
    container.querySelectorAll("[data-toggle-day]").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (typeof opts.onDayToggle === "function") {
          opts.onDayToggle(btn.dataset.toggleDay, btn.dataset.activar === "1");
        }
      });
    });
  }

  window.DeliveryFranjas = {
    fmtFecha, agruparPorFecha, apiFetch, msg, cupoChip,
    renderCalendarioSemanal, weekRange, navegarSemana,
    toISO, parseISO, addDays, startOfDay, fmtRangoSemana, slotEstado,
  };
})();
