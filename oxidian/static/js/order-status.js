(() => {
  'use strict';
  const root = document.querySelector('[data-order-state-url]');
  if (!root) return;
  const STAGES = { pendiente: 1, armando: 2, listo: 3, en_ruta: 4 };
  let timer;
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
    try {
      const response = await fetch(root.dataset.orderStateUrl, {credentials:'same-origin',cache:'no-store'});
      if (!response.ok) return;
      const state = await response.json();
      document.querySelectorAll('[data-order-status]').forEach(node => { node.textContent = state.status_label; });
      if (!state.active) { window.location.replace(state.redirect_url || '/'); return; }
      const nextStage = STAGES[state.status];
      if (nextStage) updateProgress(nextStage);
    } catch (_) { /* el ticket conserva el último estado visible sin inventar */ }
  };
  const schedule = () => { clearInterval(timer); if (!document.hidden) { refresh(); timer=setInterval(refresh,5000); } };
  document.addEventListener('visibilitychange', schedule);
  schedule();
})();
