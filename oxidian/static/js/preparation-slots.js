/* Foco de cocina por salida; las acciones conservan el envío nativo. */
(() => {
  const root = document.querySelector('[data-slotops]');
  if (!root) return;
  const turns = [...root.querySelectorAll('[data-turn]')];
  const panels = [...root.querySelectorAll('[data-panel]')];
  const empty = root.querySelector('[data-no-turn]');
  const focus = root.querySelector('[data-focusbar]');

  function select(id) {
    const active = turns.find(button => button.dataset.turn === id);
    if (!active) return;
    turns.forEach(button => {
      button.classList.toggle('is-selected', button === active);
      button.setAttribute('aria-pressed', String(button === active));
    });
    panels.forEach(panel => { panel.hidden = panel.dataset.panel !== id; });
    if (empty) empty.hidden = panels.some(panel => !panel.hidden);
    if (focus) {
      const pending = Number(active.dataset.pending || 0);
      const ready = Number(active.dataset.ready || 0);
      const title = id === 'immediate' ? 'Cola inmediata'
        : pending ? 'Prepara esta salida' : ready ? 'Salida preparada' : 'Sin pedidos en esta salida';
      const description = id === 'immediate' ? 'Atiéndela por orden de llegada.'
        : pending ? `Quedan ${pending} pedido(s). Verifica sus artículos antes de confirmar el empaque.`
          : ready ? `${ready} pedido(s) listos para entregar al rider.` : 'Elige otra salida con trabajo pendiente.';
      const heading = document.createElement('strong');
      heading.textContent = title;
      const detail = document.createElement('small');
      detail.textContent = description;
      const content = document.createElement('div');
      content.append(heading, detail);
      focus.replaceChildren(content);
    }
    try { sessionStorage.setItem('kitchen.active.slot', id); } catch (_) {}
  }

  root.addEventListener('click', event => {
    const turn = event.target.closest('[data-turn]');
    if (turn) select(turn.dataset.turn);
  });
  let saved = '';
  try { saved = sessionStorage.getItem('kitchen.active.slot') || ''; } catch (_) {}
  const initial = turns.find(button => button.dataset.turn === saved)
    || turns.find(button => button.dataset.state === 'activa') || turns[0];
  if (initial) select(initial.dataset.turn);
  // No interceptar los POST: la respuesta contiene avisos y destino de impresión.
})();
