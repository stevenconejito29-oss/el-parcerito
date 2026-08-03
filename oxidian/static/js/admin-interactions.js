/**
 * Interacciones declarativas compartidas del panel.
 *
 * La política CSP no permite atributos `onclick`/`onsubmit`. Las acciones
 * comunes se expresan con `data-*` y se resuelven aquí mediante delegación,
 * de modo que también funcionan en contenido renderizado dinámicamente.
 */
(function adminInteractions() {
  'use strict';

  document.addEventListener('submit', (event) => {
    const form = event.target.closest('form[data-confirm]');
    if (form && !window.confirm(form.dataset.confirm || '¿Continuar?')) {
      event.preventDefault();
    }
  });

  // Doble submit guard: al enviar un form marcado con `data-once-submit`,
  // deshabilita los botones y da feedback visual. Complementa la protección
  // server-side (form_token) para que el admin no pueda disparar el envío
  // dos veces (doble click, Enter rápido) y vea que algo está pasando.
  document.addEventListener('submit', (event) => {
    const form = event.target.closest('form[data-once-submit]');
    if (!form || form.dataset.submitDone === '1') return;
    form.dataset.submitDone = '1';
    form.querySelectorAll('button[type="submit"], input[type="submit"], .js-submit-once')
        .forEach((btn) => {
          btn.disabled = true;
          btn.setAttribute('aria-busy', 'true');
          btn.style.opacity = '0.7';
          btn.style.cursor = 'wait';
        });
  }, true);

  document.addEventListener('click', (event) => {
    const confirmation = event.target.closest('[data-confirm-click]');
    if (confirmation && !window.confirm(confirmation.dataset.confirmClick || '¿Continuar?')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }

    const print = event.target.closest('[data-print]');
    if (print) {
      event.preventDefault();
      window.print();
      return;
    }

    const open = event.target.closest('[data-dialog-open]');
    if (open) {
      const dialog = document.getElementById(open.dataset.dialogOpen);
      if (dialog?.showModal) dialog.showModal();
      else dialog?.classList.remove('hidden');
      return;
    }

    const close = event.target.closest('[data-dialog-close]');
    if (close) {
      const dialog = document.getElementById(close.dataset.dialogClose)
        || close.closest('dialog,[data-dialog-backdrop],.oa-dialog');
      if (dialog?.close) dialog.close();
      else dialog?.classList.add('hidden');
      return;
    }

    const backdrop = event.target.closest('[data-dialog-backdrop]');
    if (backdrop && event.target === backdrop) backdrop.classList.add('hidden');
  });

  document.addEventListener('change', (event) => {
    const control = event.target.closest('[data-submit-on-change]');
    if (control?.form) control.form.requestSubmit();
  });

  function refreshMarginPreview(form) {
    const sale = form?.querySelector('.js-sale-price');
    const cost = form?.querySelector('.js-cost-price');
    const output = form?.querySelector('.js-margin-preview');
    if (!sale || !cost || !output) return;
    const saleValue = Number.parseFloat(sale.value);
    const costValue = Number.parseFloat(cost.value);
    if (!Number.isFinite(costValue) || !Number.isFinite(saleValue) || saleValue <= 0) {
      output.textContent = 'Añade el coste para calcular la ganancia real.';
      output.style.color = 'var(--col-muted)';
      return;
    }
    const profit = saleValue - costValue;
    const margin = profit / saleValue * 100;
    output.textContent = `${profit >= 0 ? 'Ganancia' : 'Pérdida'} estimada: €${Math.abs(profit).toFixed(2)} · ${margin.toFixed(1)}%`;
    output.style.color = profit >= 0 ? 'var(--theme-success)' : 'var(--theme-danger)';
  }

  document.querySelectorAll('form:has(.js-margin-preview)').forEach(refreshMarginPreview);
  document.addEventListener('input', (event) => {
    if (event.target.matches('.js-sale-price, .js-cost-price')) {
      refreshMarginPreview(event.target.form);
    }
  });

  // `error` de imágenes no burbujea; se escucha durante captura.
  document.addEventListener('error', (event) => {
    const image = event.target;
    if (!(image instanceof HTMLImageElement)) return;
    if (image.dataset.imageFallback === 'next-template') {
      const template = image.nextElementSibling;
      if (template instanceof HTMLTemplateElement) image.replaceWith(template.content.cloneNode(true));
    } else if (image.dataset.imageFallback === 'hide') {
      image.hidden = true;
    }
  }, true);
})();
