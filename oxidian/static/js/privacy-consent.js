/* Consentimiento por categorías para cookies y almacenamiento equivalente.
 * No carga proveedores: expone una fuente única para que cualquier función
 * opcional consulte el permiso antes de escribir datos persistentes. */
(function () {
  'use strict';
  var KEY = 'oxidian.privacy.v1';
  var VERSION = 1;
  var MAX_AGE_MS = 730 * 24 * 60 * 60 * 1000;
  var banner, dialog, preferences, previousFocus;

  function read() {
    try {
      var value = JSON.parse(localStorage.getItem(KEY) || 'null');
      var decidedAt = value ? Date.parse(value.decidedAt || '') : NaN;
      var valid = value && value.version === VERSION
        && Number.isFinite(decidedAt)
        && Date.now() - decidedAt >= 0
        && Date.now() - decidedAt <= MAX_AGE_MS;
      if (!valid) {
        localStorage.removeItem(KEY);
        return null;
      }
      return value;
    } catch (_) { return null; }
  }
  function save(allowPreferences) {
    var value = {
      version: VERSION,
      necessary: true,
      preferences: !!allowPreferences,
      decidedAt: new Date().toISOString()
    };
    try {
      localStorage.setItem(KEY, JSON.stringify(value));
      if (!value.preferences) localStorage.removeItem('oxCheckoutGuest');
    } catch (_) {
      /* La navegación continúa si el navegador bloquea almacenamiento. */
    }
    banner && (banner.hidden = true);
    close();
    window.dispatchEvent(new CustomEvent('ox:privacy-changed', { detail: value }));
    return value;
  }
  function has(category) {
    if (category === 'necessary') return true;
    var value = read();
    return !!(value && value[category] === true);
  }
  function open() {
    if (!dialog) return;
    previousFocus = document.activeElement;
    preferences.checked = has('preferences');
    dialog.hidden = false;
    document.body.classList.add('ox-privacy-open');
    dialog.querySelector('[data-privacy-save]')?.focus();
  }
  function close() {
    if (!dialog || dialog.hidden) return;
    dialog.hidden = true;
    document.body.classList.remove('ox-privacy-open');
    previousFocus?.focus?.();
  }
  function init() {
    banner = document.getElementById('ox-privacy-banner');
    dialog = document.getElementById('ox-privacy-dialog');
    preferences = document.getElementById('ox-privacy-preferences');
    if (!banner || !dialog || !preferences) return;
    banner.hidden = !!read();
    document.querySelectorAll('[data-privacy-open]').forEach(function (el) {
      el.addEventListener('click', open);
    });
    document.querySelectorAll('[data-privacy-close]').forEach(function (el) {
      el.addEventListener('click', close);
    });
    document.querySelectorAll('[data-privacy-reject]').forEach(function (el) {
      el.addEventListener('click', function () { save(false); });
    });
    document.querySelectorAll('[data-privacy-accept]').forEach(function (el) {
      el.addEventListener('click', function () { save(true); });
    });
    dialog.querySelector('[data-privacy-save]')?.addEventListener('click', function () {
      save(preferences.checked);
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !dialog.hidden) close();
    });
  }
  window.OxPrivacy = { read: read, has: has, save: save, open: open };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
