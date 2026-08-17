(() => {
  'use strict';
  const page = document.querySelector('.favor-page[data-favor-signature]');
  if (!page) return;
  // Si HTMX está cargado, el wrapper #favor-cards se auto-refresca vía
  // hx-trigger="every 8s" sin recargar la página. Este fallback JS solo
  // actúa cuando HTMX no está disponible (CSP bloqueado, offline, error de
  // carga). Se mantiene por robustez de UX en caso de degradación.
  if (window.htmx && document.getElementById('favor-cards')) {
    /* HTMX hace el polling; no duplicamos. */
  } else {
    let signature = page.dataset.favorSignature;
    let busy = false;
    const refresh = async () => {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const response = await fetch('/api/favor/state', {credentials:'same-origin', cache:'no-store'});
        const data = await response.json();
        if (data.ok && data.signature !== signature) {
          signature = data.signature;
          if (!document.activeElement?.matches('input, textarea, select')) location.reload();
        }
      } catch (_) { /* Se reintenta sin interrumpir al cliente. */ }
      finally { busy = false; }
    };
    window.setInterval(refresh, 8000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  }

  document.querySelectorAll('[data-address-autocomplete]').forEach(input => {
    const box = input.closest('.favor-address-field')?.querySelector('.favor-suggestions');
    let timer = 0, controller = null;
    input.addEventListener('input', () => {
      clearTimeout(timer); controller?.abort();
      const query = input.value.trim();
      if (query.length < 4) { box?.replaceChildren(); return; }
      timer = window.setTimeout(async () => {
        controller = new AbortController();
        try {
          const response = await fetch(`/api/geocode/suggest?q=${encodeURIComponent(query)}`, {signal:controller.signal, cache:'no-store'});
          const data = await response.json();
          box.replaceChildren();
          (data.results || []).slice(0, 5).forEach(result => {
            const option = document.createElement('button');
            option.type = 'button'; option.setAttribute('role', 'option');
            option.textContent = result.label;
            option.addEventListener('click', () => { input.value = result.value; box.replaceChildren(); input.focus(); });
            box.append(option);
          });
        } catch (error) { if (error.name !== 'AbortError') box?.replaceChildren(); }
      }, 280);
    });
    input.addEventListener('blur', () => window.setTimeout(() => box?.replaceChildren(), 180));
  });
  const detail = document.querySelector('textarea[name="item_description"]');
  document.querySelectorAll('[data-cruce-example]').forEach(button => button.addEventListener('click', () => {
    if (!detail) return;
    detail.value = button.dataset.cruceExample || '';
    detail.focus({preventScroll:true});
    button.closest('.favor-examples')?.querySelectorAll('button').forEach(item => item.classList.toggle('is-selected', item === button));
  }));
})();
