/* Oxidian PWA runtime
 * Una sola implementación para storefront y paneles operativos.
 * Gestiona instalación, actualización, Web Push, avisos en primer plano,
 * conectividad, almacenamiento persistente y app badge.
 */
(function () {
  'use strict';

  const meta = name => document.querySelector(`meta[name="${name}"]`)?.content || '';
  const mode = meta('ox-pwa-mode') || 'public';
  const pushEligible = meta('ox-push-eligible') === '1';
  const operational = meta('ox-pwa-operational') === '1';
  const csrfToken = meta('ox-csrf-token');
  const cartCount = Number.parseInt(meta('ox-cart-count') || '0', 10) || 0;
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isInAppBrowser = /Instagram|FBAN|FBAV|Line\/|TikTok|Twitter/i.test(navigator.userAgent);
  const isStandalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const installButtons = () => document.querySelectorAll('[data-pwa-install], #ox-staff-install');
  let deferredInstallPrompt = null;
  let reloadForUpdate = false;
  let registration = null;
  let audioContext = null;
  let wakeLock = null;
  let offlineNotice = null;

  function toast(message, type = 'info', action = null, duration = 5200) {
    let wrap = document.getElementById('ox-toasts');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'ox-toasts';
      wrap.className = 'ox-toast-wrap';
      document.body.appendChild(wrap);
    }
    const item = document.createElement('div');
    item.className = `ox-toast ox-toast-${type}`;
    item.setAttribute('role', type === 'danger' ? 'alert' : 'status');
    const copy = document.createElement('span');
    copy.textContent = message;
    item.appendChild(copy);
    if (action) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ox-pwa-toast-action';
      button.textContent = action.label;
      button.addEventListener('click', action.run, { once: true });
      item.appendChild(button);
    }
    wrap.appendChild(item);
    if (duration > 0) window.setTimeout(() => item.remove(), duration);
    return item;
  }

  function setAppBadge(value) {
    if (!('setAppBadge' in navigator)) return;
    Promise.resolve(value > 0 ? navigator.setAppBadge(value) : navigator.clearAppBadge()).catch(() => {});
  }

  function showInstallSheet({ automatic = false, force = false } = {}) {
    const sheet = document.getElementById('ox-pwa-sheet');
    if (!sheet) return;
    const dismissed = Number.parseInt(localStorage.getItem('oxPwaDismissedAt') || '0', 10);
    if (!force && dismissed && Date.now() - dismissed < 7 * 86400 * 1000) return;
    sheet.hidden = false;
    sheet.classList.toggle('is-soft', automatic);
  }

  function hideInstallUi() {
    installButtons().forEach(button => {
      button.hidden = true;
      button.classList.add('ox-pwa-installed');
    });
    document.body.classList.remove('ox-install-visible');
    document.getElementById('ox-pwa-sheet')?.setAttribute('hidden', '');
  }

  function prepareInstallUi() {
    if (isStandalone()) {
      hideInstallUi();
      return;
    }
    installButtons().forEach(button => {
      button.hidden = false;
      button.classList.remove('ox-pwa-installed');
    });
    document.body.classList.add('ox-install-visible');

    const copy = document.querySelector('.ox-pwa-text');
    const action = document.querySelector('.ox-pwa-action .ox-install-text');
    if (isInAppBrowser) {
      if (copy) copy.textContent = 'Abre esta página en Safari o Chrome para instalar la app.';
      if (action) action.textContent = 'Cómo abrirla';
    } else if (isIOS) {
      if (copy) copy.textContent = 'En Safari toca Compartir y después “Añadir a pantalla de inicio”.';
      if (action) action.textContent = 'Ver cómo';
      window.addEventListener('load', () => setTimeout(() => showInstallSheet({ automatic: true }), 2200), { once: true });
    } else {
      if (copy) copy.textContent = 'Instala la app para abrir el menú más rápido y recibir avisos compatibles con tu dispositivo.';
    }
  }

  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    deferredInstallPrompt = event;
    prepareInstallUi();
    if (mode === 'public') setTimeout(() => showInstallSheet({ automatic: true }), 1800);
  });

  window.addEventListener('appinstalled', () => {
    deferredInstallPrompt = null;
    localStorage.setItem('oxPwaInstalled', '1');
    hideInstallUi();
    toast('Aplicación instalada. Ya puedes abrirla desde tu pantalla de inicio.', 'success');
  });

  document.addEventListener('click', async event => {
    const install = event.target.closest('[data-pwa-install], #ox-staff-install');
    if (install) {
      event.preventDefault();
      if (isStandalone()) return hideInstallUi();
      if (deferredInstallPrompt) {
        deferredInstallPrompt.prompt();
        await deferredInstallPrompt.userChoice.catch(() => null);
        deferredInstallPrompt = null;
        document.getElementById('ox-pwa-sheet')?.setAttribute('hidden', '');
      } else if (mode === 'public') {
        if (install.closest('#ox-pwa-sheet')) {
          document.getElementById('ox-pwa-sheet')?.setAttribute('hidden', '');
          toast(
            isInAppBrowser
              ? 'Abre esta página en Safari o Chrome y usa la opción de instalar.'
              : 'Abre el menú del navegador y elige “Instalar aplicación” o “Añadir a pantalla de inicio”.',
            'info', null, 8000,
          );
        } else {
          showInstallSheet({ force: true });
        }
      } else {
        toast(
          isIOS
            ? 'En Safari: Compartir → Añadir a pantalla de inicio.'
            : 'Abre el menú del navegador y elige “Instalar aplicación”.',
          'info', null, 8000,
        );
      }
      return;
    }

    if (event.target.closest('[data-pwa-dismiss]')) {
      localStorage.setItem('oxPwaDismissedAt', String(Date.now()));
      document.getElementById('ox-pwa-sheet')?.setAttribute('hidden', '');
      return;
    }

    const pushButton = event.target.closest('[data-push-activate], [data-push-enable]');
    if (pushButton) {
      event.preventDefault();
      await enablePush(pushButton);
      return;
    }

    if (event.target.closest('[data-push-dismiss]')) {
      localStorage.setItem('oxPushDismissedAt', String(Date.now()));
      sessionStorage.setItem('ox.pushPromptDismissed', '1');
      event.target.closest('#ox-push-prompt, #ox-push-banner')?.setAttribute('hidden', '');
      return;
    }

    const storageButton = event.target.closest('[data-storage-prepare]');
    if (storageButton) await prepareStorage(storageButton);
  });

  function urlB64ToBytes(value) {
    const padding = '='.repeat((4 - value.length % 4) % 4);
    const raw = atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, character => character.charCodeAt(0));
  }

  function sameBytes(left, right) {
    if (!left || !right) return false;
    const a = new Uint8Array(left);
    const b = new Uint8Array(right);
    return a.length === b.length && a.every((value, index) => value === b[index]);
  }

  async function subscribePush(reg) {
    const keyResponse = await fetch('/api/push/vapid-key', {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    const keyPayload = await keyResponse.json().catch(() => ({}));
    if (!keyResponse.ok || !keyPayload.public_key) {
      throw new Error(keyPayload.error || 'Las notificaciones no están configuradas en el servidor.');
    }

    const applicationServerKey = urlB64ToBytes(keyPayload.public_key);
    let subscription = await reg.pushManager.getSubscription();
    if (subscription && !sameBytes(subscription.options?.applicationServerKey, applicationServerKey)) {
      await subscription.unsubscribe();
      subscription = null;
    }
    if (!subscription) {
      subscription = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey });
    }

    const response = await fetch('/api/push/subscribe', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify(subscription.toJSON()),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || 'No se pudo registrar este dispositivo.');
    }
    localStorage.setItem('oxPushEnabled', '1');
    localStorage.setItem('oxPushSound', '1');
    setPushUi('active');
    return subscription;
  }

  async function enablePush(button) {
    if (!pushEligible) {
      toast('Podrás activar avisos de seguimiento al confirmar tu primer pedido.', 'info');
      return;
    }
    if (!window.isSecureContext || !('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
      toast('Este navegador no admite Web Push. Usa la versión reciente de Safari, Chrome o Edge mediante HTTPS.', 'warning', null, 8000);
      return;
    }
    if (isIOS && !isStandalone()) {
      showInstallSheet({ force: true });
      toast('En iPhone, instala primero la app en la pantalla de inicio y actívala desde allí.', 'info', null, 8000);
      return;
    }
    if (Notification.permission === 'denied') {
      setPushUi('denied');
      toast('Los avisos están bloqueados. Actívalos en los ajustes del sitio o de la app.', 'warning', null, 9000);
      return;
    }

    const original = button.textContent;
    button.disabled = true;
    button.textContent = 'Activando…';
    try {
      unlockAudio();
      const permission = Notification.permission === 'granted'
        ? 'granted'
        : await Notification.requestPermission();
      if (permission !== 'granted') {
        setPushUi(permission === 'denied' ? 'denied' : 'default');
        toast('No se activaron los avisos. Puedes intentarlo cuando quieras.', 'warning');
        return;
      }
      const reg = registration || await navigator.serviceWorker.ready;
      await subscribePush(reg);
      toast('Avisos activados para este dispositivo.', 'success');
      chime();
    } catch (error) {
      setPushUi('error', error.message);
      toast(error.message || 'No se pudieron activar los avisos.', 'danger', null, 9000);
    } finally {
      button.disabled = false;
      if (button.isConnected && button.textContent === 'Activando…') button.textContent = original;
    }
  }

  function setPushUi(state, detail = '') {
    const prompt = document.getElementById('ox-push-prompt');
    const banner = document.getElementById('ox-push-banner');
    const root = prompt || banner;
    if (!root) return;
    const title = root.querySelector('strong, .ox-push-prompt__copy p');
    const copy = root.querySelector('small');
    const button = root.querySelector('[data-push-activate], [data-push-enable]');
    if (state === 'active') {
      root.hidden = true;
      document.querySelectorAll('[data-push-activate], [data-push-enable]').forEach(item => {
        item.textContent = 'Avisos activos';
        item.setAttribute('aria-pressed', 'true');
      });
      return;
    }
    root.hidden = false;
    if (state === 'denied') {
      if (title) title.textContent = 'Avisos bloqueados';
      if (copy) copy.textContent = 'Permítelos desde los ajustes del navegador o de la app.';
      if (button) button.textContent = 'Ver solución';
    } else if (state === 'error') {
      if (title) title.textContent = 'No se pudieron activar';
      if (copy) copy.textContent = detail || 'Comprueba la conexión y vuelve a intentarlo.';
      if (button) button.textContent = 'Reintentar';
    }
  }

  function unlockAudio() {
    if (!window.AudioContext && !window.webkitAudioContext) return;
    if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
    if (audioContext.state === 'suspended') audioContext.resume().catch(() => {});
  }

  function chime() {
    if (localStorage.getItem('oxPushSound') !== '1' || !audioContext || audioContext.state !== 'running') return;
    const now = audioContext.currentTime;
    [0, .14].forEach((delay, index) => {
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.type = 'sine';
      oscillator.frequency.value = index ? 880 : 660;
      gain.gain.setValueAtTime(.0001, now + delay);
      gain.gain.exponentialRampToValueAtTime(.12, now + delay + .015);
      gain.gain.exponentialRampToValueAtTime(.0001, now + delay + .12);
      oscillator.connect(gain).connect(audioContext.destination);
      oscillator.start(now + delay);
      oscillator.stop(now + delay + .13);
    });
  }

  async function prepareStorage(button) {
    const status = document.getElementById('ox-app-tools-status');
    button.disabled = true;
    try {
      const persistent = navigator.storage?.persist ? await navigator.storage.persist() : false;
      const message = persistent
        ? 'El sistema conservará los recursos esenciales de la app.'
        : 'El navegador administrará el espacio de la app automáticamente.';
      if (status) status.textContent = message;
      toast(message, 'info');
    } catch (_) {
      if (status) status.textContent = 'El almacenamiento lo administra el navegador.';
    } finally {
      button.disabled = false;
    }
  }

  function announceUpdate(reg) {
    if (!reg.waiting || document.querySelector('[data-pwa-update-toast]')) return;
    const notice = toast('Hay una versión nueva lista.', 'info', {
      label: 'Actualizar',
      run: () => {
        reloadForUpdate = true;
        reg.waiting?.postMessage({ type: 'SKIP_WAITING' });
      },
    }, 0);
    notice.dataset.pwaUpdateToast = '1';
  }

  async function registerServiceWorker() {
    if (!window.isSecureContext || !('serviceWorker' in navigator)) return;
    try {
      registration = await navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' });
      announceUpdate(registration);
      registration.addEventListener('updatefound', () => {
        const worker = registration.installing;
        worker?.addEventListener('statechange', () => {
          if (worker.state === 'installed' && navigator.serviceWorker.controller) announceUpdate(registration);
        });
      });
      if (pushEligible && Notification.permission === 'granted') {
        await subscribePush(registration).catch(error => setPushUi('error', error.message));
      }
      let lastUpdate = 0;
      const update = () => {
        if (Date.now() - lastUpdate < 60000) return;
        lastUpdate = Date.now();
        registration.update().then(() => announceUpdate(registration)).catch(() => {});
      };
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') update();
      });
      window.setInterval(update, 60 * 60 * 1000);
    } catch (error) {
      if (isStandalone()) toast('No se pudo iniciar el modo app. Comprueba la conexión.', 'warning');
    }
  }

  async function maintainWakeLock() {
    if (!operational || !isStandalone() || document.visibilityState !== 'visible' || !('wakeLock' in navigator)) return;
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; }, { once: true });
    } catch (_) {
      // El sistema puede negarlo por ahorro de batería; la operación continúa.
    }
  }

  navigator.serviceWorker?.addEventListener('controllerchange', () => {
    if (reloadForUpdate) location.reload();
  });

  navigator.serviceWorker?.addEventListener('message', event => {
    if (event.data?.type === 'OX_PUSH_SUBSCRIPTION_CHANGED') {
      if (pushEligible && Notification.permission === 'granted') {
        navigator.serviceWorker.ready.then(subscribePush).catch(() => {});
      }
      return;
    }
    if (event.data?.type !== 'OX_PUSH_RECEIVED') return;
    const payload = event.data.payload || {};
    toast([payload.title, payload.body].filter(Boolean).join(' · '), 'info', payload.url ? {
      label: 'Ver',
      run: () => { location.href = payload.url; },
    } : null, 9000);
    chime();
  });

  window.addEventListener('offline', () => {
    offlineNotice ||= toast('Sin conexión. Conservamos tus datos; las acciones se confirmarán al recuperar internet.', 'warning', null, 0);
  });
  window.addEventListener('online', () => {
    offlineNotice?.remove();
    offlineNotice = null;
    toast('Conexión recuperada.', 'success');
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !wakeLock) maintainWakeLock();
  });
  document.addEventListener('pointerdown', () => {
    if (localStorage.getItem('oxPushSound') === '1') unlockAudio();
  }, { once: true, passive: true });

  function preparePushUi() {
    if (!pushEligible || !('Notification' in window)) return;
    const root = document.getElementById(mode === 'staff' ? 'ox-push-banner' : 'ox-push-prompt');
    if (!root) return;
    if (Notification.permission === 'granted') return;
    if (Notification.permission === 'denied') return setPushUi('denied');
    if (mode === 'staff' && sessionStorage.getItem('ox.pushPromptDismissed') === '1') return;
    const dismissed = Number.parseInt(localStorage.getItem('oxPushDismissedAt') || '0', 10);
    if (dismissed && Date.now() - dismissed < 7 * 86400 * 1000) return;
    if (mode === 'staff') {
      const page = document.querySelector('.ox-page');
      if (page && root.parentElement !== page) page.prepend(root);
      root.classList.add('is-inline');
      root.hidden = false;
    } else {
      setTimeout(() => { root.hidden = false; }, 6000);
    }
  }

  prepareInstallUi();
  preparePushUi();
  setAppBadge(cartCount);
  maintainWakeLock();
  window.addEventListener('load', registerServiceWorker, { once: true });
})();
