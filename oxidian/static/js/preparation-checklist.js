/* Comprobación de productos. El almacenamiento es una ayuda opcional. */
(function () {
  'use strict';
  document.querySelectorAll('form[data-prep-confirm-ready]').forEach((form) => {
    const card = form.closest('.work-card');
    if (!card) return;
    const items = Array.from(card.querySelectorAll('[data-item-check]'));
    const button = form.querySelector('[data-ready-button]');
    const key = `prep_checklist_v2_${form.dataset.pedidoId}`;
    let saved = [];
    try {
      const stored = JSON.parse(localStorage.getItem(key) || '[]');
      if (Array.isArray(stored)) saved = stored;
    } catch (_) {}
    function update() {
      const count = items.filter((item) => item.checked).length;
      items.forEach((item) => item.closest('.work-item')?.classList.toggle('is-checked', item.checked));
      if (!button) return;
      button.disabled = count !== items.length;
      button.setAttribute('aria-disabled', String(button.disabled));
      button.textContent = button.disabled ? `Comprobar productos · ${count}/${items.length}` : '✓ Todo comprobado · dejar listo';
    }
    items.forEach((item) => {
      item.checked = saved.includes(item.dataset.itemKey);
      item.addEventListener('change', () => {
        update();
        try {
          localStorage.setItem(key, JSON.stringify(items.filter((entry) => entry.checked).map((entry) => entry.dataset.itemKey)));
        } catch (_) {}
        if (item.checked && navigator.vibrate) navigator.vibrate(15);
      });
    });
    form.addEventListener('submit', (event) => {
      if (items.some((item) => !item.checked)) {
        event.preventDefault();
        items.find((item) => !item.checked)?.focus();
      }
      // Conserva las marcas si el servidor rechaza el cambio o falla la red.
    });
    update();
  });
})();
