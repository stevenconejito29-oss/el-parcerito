(() => {
  'use strict';
  const root = document.querySelector('[data-order-state-url]');
  if (!root) return;
  const STAGES = { pendiente: 1, armando: 2, listo: 3, en_ruta: 4 };
  let timer;
  let refreshing = false;
  let finished = false;
  // Actualiza el bloque de progreso sin recargar toda la página. Devuelve
  // true si aplicó cambios. Requiere el mismo DOM que renderiza el server
  // en pedido_confirmado.html:45-53.
  const updateProgress = (nextStage) => {
    const progress = document.querySelector('[data-order-progress]');
    if (!progress) return false;
    const current = Number(progress.dataset.orderProgress) || 0;
    if (current === nextStage) return false;
    progress.dataset.orderProgress = String(nextStage);
    const steps = progress.querySelectorAll(':scope > div');
    steps.forEach((step, index) => {
      const num = index + 1;
      step.classList.toggle('is-done', num < nextStage);
      step.classList.toggle('is-current', num === nextStage);
      // Reemplaza el número con ✓ si ya está hecho, sin tocar el resto del texto.
      const bullet = step.querySelector('b');
      if (bullet) bullet.textContent = num < nextStage ? '✓' : String(num);
    });
    // Anima suavemente el cambio (usa motion-pulse definido en motion.css).
    const currentStep = steps[nextStage - 1];
    if (currentStep) {
      currentStep.classList.remove('motion-pulse');
      requestAnimationFrame(() => currentStep.classList.add('motion-pulse'));
      setTimeout(() => currentStep.classList.remove('motion-pulse'), 3200);
    }
    return true;
  };
  const refresh = async () => {
    if (refreshing || finished || document.hidden) return;
    refreshing = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(root.dataset.orderStateUrl, {credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if (!response.ok) throw new Error('Estado no disponible');
      const state = await response.json();
      if (state.ok !== true) throw new Error('Respuesta no válida');
      const connection = root.querySelector('[data-order-connection]');
      if (connection) {
        connection.textContent = 'Estado actualizado';
        connection.parentElement.dataset.connection = 'updated';
      }
      document.querySelectorAll('[data-order-status]').forEach(node => { node.textContent = state.status_label; });
      if (state.presentation) {
        const view = state.presentation;
        const title = root.querySelector('[data-order-title]');
        const description = root.querySelector('[data-order-description]');
        if (title) title.textContent = view.title;
        if (description) description.textContent = view.description;
        const alert = root.querySelector('.order-verification-alert');
        if (alert) alert.hidden = !view.confirmation_pending;
        const eyebrow = root.querySelector('[data-order-eyebrow]');
        if (eyebrow) eyebrow.textContent = view.confirmation_pending ? 'Falta tu confirmación' : 'Seguimiento del pedido';
        const validation = root.querySelector('[data-order-validation]');
        if (validation) validation.textContent = view.confirmation_pending ? 'Pendiente de confirmar' : 'Pedido validado';
        if (typeof view.payment_label === 'string') root.querySelectorAll('[data-order-payment-label]').forEach(node => { node.textContent = view.payment_label; });
        if (typeof view.payment_status === 'string') root.querySelectorAll('[data-order-payment-status]').forEach(node => { node.textContent = view.payment_status; });
        const paymentInstructions = root.querySelector('[data-order-payment-instructions]');
        if (paymentInstructions && typeof view.payment_confirmed === 'boolean') paymentInstructions.hidden = view.payment_confirmed;
      }
      if (!state.active) { finished = true; window.location.replace(state.redirect_url || '/'); return; }
      const nextStage = STAGES[state.status];
      if (nextStage) updateProgress(nextStage);
    } catch (_) {
      const connection = root.querySelector('[data-order-connection]');
      if (connection) {
        connection.textContent = 'Sin actualizar. Reintentando…';
        connection.parentElement.dataset.connection = 'error';
      }
    } finally {
      clearTimeout(timeout);
      refreshing = false;
      clearTimeout(timer);
      if (!finished && !document.hidden) timer = setTimeout(refresh, 5000);
    }
  };
  // Una petición a la vez: una respuesta lenta no puede sobrescribir otra más reciente.
  const schedule = () => { clearTimeout(timer); if (!document.hidden) refresh(); };
  document.addEventListener('visibilitychange', schedule);
  schedule();
})();
