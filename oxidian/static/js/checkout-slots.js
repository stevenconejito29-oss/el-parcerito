/* El cliente elige día y horario; el servidor vuelve a validar disponibilidad. */
(() => {
  'use strict';
  const block = document.getElementById('franjas-slot-block');
  if (!block || block.dataset.active !== '1') return;
  const DF = window.DeliveryFranjas;
  const day = document.getElementById('franjas-day');
  const list = document.getElementById('franjas-slot-container');
  const selected = document.getElementById('franjas-slot-id');
  const summary = document.getElementById('franjas-slot-selected');
  const error = document.getElementById('franjas-slot-error');
  const retry = document.getElementById('franjas-slot-retry');
  const choice = document.getElementById('delivery-plan-choice');
  let slots = [];
  const delivery = () => document.querySelector('[name="tipo_entrega_cliente"]:checked')?.value === 'delivery';
  const wantsSlot = () => delivery() && (document.querySelector('[name="delivery_plan_ui"]:checked')?.value || (block.dataset.immediate === '1' ? 'inmediato' : 'franja')) === 'franja';
  function clearSelection() {
    selected.value = '';
    summary.hidden = true;
    list.querySelectorAll('input').forEach(input => {input.checked = false;});
    list.querySelectorAll('.df-slot-selected').forEach(el => el.classList.remove('df-slot-selected'));
  }
  function visibility() {
    if (choice) choice.hidden = !delivery();
    block.hidden = !wantsSlot();
    day.required = wantsSlot();
    if (!wantsSlot()) clearSelection();
  }
  function renderTimes() {
    clearSelection();
    list.replaceChildren();
    error.hidden = true;
    for (const slot of slots.filter(s => s.fecha === day.value)) {
      const label = document.createElement('label');
      label.className = 'df-slot df-slot-selectable';
      const input = document.createElement('input');
      input.type = 'radio'; input.name = 'delivery_slot_ui'; input.value = String(slot.id);
      const text = document.createElement('span');
      text.textContent = `${slot.hora_inicio}–${slot.hora_fin}`;
      input.addEventListener('change', () => {
        list.querySelectorAll('.df-slot-selected').forEach(el => el.classList.remove('df-slot-selected'));
        label.classList.add('df-slot-selected');
        selected.value = input.value;
        summary.textContent = `Entrega: ${DF.fmtFecha(slot.fecha)}, ${text.textContent}`;
        summary.hidden = false; error.hidden = true;
      });
      label.append(input, text); list.append(label);
    }
  }
  async function load() {
    clearSelection(); slots = []; list.replaceChildren(); error.hidden = true; retry.hidden = true;
    day.replaceChildren(new Option('Cargando días disponibles…', '')); day.disabled = true;
    try {
      const result = await DF.apiFetch(block.dataset.url);
      if (!result.ok || !Array.isArray(result.data?.franjas)) throw new Error('unavailable');
      slots = result.data.franjas.filter(slot => slot.disponible);
      day.replaceChildren(new Option(slots.length ? 'Selecciona un día' : 'Sin horarios disponibles', ''));
      for (const [date] of DF.agruparPorFecha(slots)) day.add(new Option(DF.fmtFecha(date), date));
      day.disabled = !slots.length;
      if (!slots.length) {
        error.textContent = 'No hay horarios disponibles. Consulta más tarde o elige otra forma de entrega.';
        error.hidden = false; retry.hidden = false;
      }
    } catch (_) {
      day.replaceChildren(new Option('No se pudieron cargar los días', ''));
      error.textContent = 'No pudimos cargar los horarios. Vuelve a intentarlo antes de confirmar.';
      error.hidden = false; retry.hidden = false;
    }
    visibility();
  }
  day.addEventListener('change', renderTimes);
  retry.addEventListener('click', load);
  document.querySelectorAll('[name="tipo_entrega_cliente"], [name="delivery_plan_ui"]').forEach(input => input.addEventListener('change', visibility));
  document.getElementById('form-checkout')?.addEventListener('submit', event => {
    if (wantsSlot() && !selected.value) {
      event.preventDefault();
      error.textContent = 'Selecciona el día y una franja disponible para continuar.';
      error.hidden = false; block.hidden = false;
      block.scrollIntoView({block:'center', behavior:'smooth'});
    }
  }, true);
  visibility(); load();
})();
