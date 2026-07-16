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
