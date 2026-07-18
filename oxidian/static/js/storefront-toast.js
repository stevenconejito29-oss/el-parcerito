/* storefront-toast.js
 * Sistema unificado de toast + modal de confirmación.
 * Reemplaza alert()/confirm() y los flash messages estáticos.
 *
 * API global:
 *   window.OxToast.show(msg, type='info', ttl=3200)
 *   window.OxToast.confirm({title, body, confirmText, cancelText}) -> Promise<boolean>
 *
 * Estilo: se apoya en .ox-toast / .ox-modal en CSS. Respeta safe-area-inset
 * vía variable --safe-bottom (ver storefront-viewport.js).
 */
(function () {
  'use strict';

  var stack = null;
  function ensureStack() {
    if (stack && document.body.contains(stack)) return stack;
    stack = document.getElementById('ox-toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.id = 'ox-toast-stack';
      stack.className = 'ox-toast-stack';
      stack.setAttribute('role', 'status');
      stack.setAttribute('aria-live', 'polite');
      document.body.appendChild(stack);
    }
    return stack;
  }

  var ICONS = {
    success: 'mariposa',
    danger: 'alerta',
    error: 'alerta',
    warning: 'alerta',
    info: 'cafecito'
  };

  function heritageIcon(name) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'ox-heritage-icon');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    var use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#ox-hi-' + name);
    svg.appendChild(use);
    return svg;
  }

  function show(msg, type, ttl) {
    if (!msg) return;
    type = type || 'info';
    if (typeof ttl !== 'number') ttl = 3200;
    var host = ensureStack();
    var el = document.createElement('div');
    el.className = 'ox-toast-v2 ox-toast-v2-' + type;
    el.setAttribute('role', 'status');
    var icon = document.createElement('span');
    icon.className = 'ox-toast-v2__icon';
    icon.appendChild(heritageIcon(ICONS[type] || ICONS.info));
    var text = document.createElement('span');
    text.className = 'ox-toast-v2__text';
    text.textContent = String(msg);
    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'ox-toast-v2__close';
    close.setAttribute('aria-label', 'Cerrar');
    close.textContent = '×';
    el.appendChild(icon);
    el.appendChild(text);
    el.appendChild(close);
    host.appendChild(el);

    requestAnimationFrame(function () {
      el.classList.add('is-in');
    });

    var timer = null;
    function dismiss() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      el.classList.remove('is-in');
      el.classList.add('is-out');
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 260);
    }
    close.addEventListener('click', dismiss);
    if (ttl > 0) timer = setTimeout(dismiss, ttl);
    return { dismiss: dismiss };
  }

  function confirmDialog(opts) {
    opts = opts || {};
    var title = opts.title || '¿Confirmar?';
    var body = opts.body || '';
    var confirmText = opts.confirmText || 'Sí';
    var cancelText = opts.cancelText || 'Cancelar';
    var danger = !!opts.danger;

    return new Promise(function (resolve) {
      var backdrop = document.createElement('div');
      backdrop.className = 'ox-modal-backdrop';
      backdrop.setAttribute('role', 'dialog');
      backdrop.setAttribute('aria-modal', 'true');
      backdrop.innerHTML =
        '<div class="ox-modal" role="document">' +
        '<div class="ox-modal__heritage" aria-hidden="true"><svg class="ox-heritage-icon" viewBox="0 0 24 24"><use href="#ox-hi-sombrero"></use></svg></div>' +
        '<h3 class="ox-modal__title"></h3>' +
        '<div class="ox-modal__body"></div>' +
        '<div class="ox-modal__actions">' +
        '<button type="button" class="ox-modal__btn ox-modal__btn--ghost" data-action="cancel"></button>' +
        '<button type="button" class="ox-modal__btn ' + (danger ? 'ox-modal__btn--danger' : 'ox-modal__btn--primary') + '" data-action="confirm"></button>' +
        '</div>' +
        '</div>';
      backdrop.querySelector('.ox-modal__title').textContent = title;
      backdrop.querySelector('.ox-modal__body').textContent = body;
      backdrop.querySelector('[data-action="cancel"]').textContent = cancelText;
      backdrop.querySelector('[data-action="confirm"]').textContent = confirmText;

      function close(value) {
        document.removeEventListener('keydown', onKey);
        backdrop.classList.remove('is-in');
        setTimeout(function () {
          if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
          resolve(value);
        }, 200);
      }
      function onKey(e) {
        if (e.key === 'Escape') close(false);
        else if (e.key === 'Enter') close(true);
      }

      backdrop.addEventListener('click', function (e) {
        if (e.target === backdrop) close(false);
      });
      backdrop.querySelector('[data-action="cancel"]').addEventListener('click', function () { close(false); });
      backdrop.querySelector('[data-action="confirm"]').addEventListener('click', function () { close(true); });
      document.addEventListener('keydown', onKey);

      document.body.appendChild(backdrop);
      requestAnimationFrame(function () { backdrop.classList.add('is-in'); });
      var btn = backdrop.querySelector('[data-action="confirm"]');
      if (btn) btn.focus();
    });
  }

  // Migración suave: los flash messages viejos (con .ox-toast) se autodismissan.
  document.addEventListener('DOMContentLoaded', function () {
    var legacy = document.querySelectorAll('#ox-toasts .ox-toast');
    legacy.forEach(function (n, i) {
      setTimeout(function () {
        n.style.transition = 'opacity .25s, transform .25s';
        n.style.opacity = '0';
        n.style.transform = 'translateY(-8px)';
        setTimeout(function () { n.remove(); }, 280);
      }, 4200 + i * 250);
    });
  });

  window.OxToast = { show: show, confirm: confirmDialog };
})();
