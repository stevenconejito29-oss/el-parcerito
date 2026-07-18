/* Estado visual único de la Canasta.
 * La sesión Flask sigue siendo la autoridad; este módulo solo refleja el conteo
 * exacto devuelto por el servidor en cabecera, navegación y badge de la PWA. */
(function () {
  'use strict';

  function normalizedCount(value) {
    const count = Number.parseInt(value, 10);
    return Number.isFinite(count) && count > 0 ? count : 0;
  }

  function setTextBadge(parent, selector, className, attribute, count) {
    if (!parent) return;
    let badge = parent.querySelector(selector);
    if (!count) {
      badge?.remove();
      return;
    }
    if (!badge) {
      badge = document.createElement('span');
      badge.className = className;
      badge.setAttribute(attribute, '');
      parent.appendChild(badge);
    }
    badge.textContent = String(count);
  }

  function syncAppBadge(count) {
    if (!('setAppBadge' in navigator)) return;
    try {
      if (count) navigator.setAppBadge(count);
      else navigator.clearAppBadge();
    } catch (_) { /* API opcional: nunca debe afectar la compra. */ }
  }

  function setCount(value, options) {
    const count = normalizedCount(value);
    const headerCart = document.querySelector('.ox-header-cart');
    const bottomCart = document.querySelector('.ox-bnav-cart');
    const bottomIcon = bottomCart?.querySelector('.ox-bnav-svg');

    headerCart?.classList.toggle('has-items', count > 0);
    bottomCart?.classList.toggle('has-items', count > 0);
    setTextBadge(headerCart, '[data-cart-badge]', 'ox-header-cart-count', 'data-cart-badge', count);
    setTextBadge(bottomIcon, '[data-bnav-badge]', 'ox-bnav-badge', 'data-bnav-badge', count);

    const meta = document.querySelector('meta[name="ox-cart-count"]');
    if (meta) meta.content = String(count);
    syncAppBadge(count);

    if (options?.bump && count) {
      bottomCart?.classList.remove('is-bumping');
      requestAnimationFrame(() => bottomCart?.classList.add('is-bumping'));
      window.setTimeout(() => bottomCart?.classList.remove('is-bumping'), 520);
    }
    document.dispatchEvent(new CustomEvent('oxcart:updated', { detail: { count } }));
    return count;
  }

  function initialCount() {
    return normalizedCount(document.querySelector('meta[name="ox-cart-count"]')?.content);
  }

  window.OxCartUI = { setCount, getCount: initialCount };
  const init = () => setCount(initialCount());
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
