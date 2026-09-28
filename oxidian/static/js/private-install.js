(() => {
  'use strict';
  const root = document.querySelector('[data-app-required="1"]');
  if (!root) return;
  const installed = navigator.standalone === true || ['standalone','fullscreen','minimal-ui'].some(mode => matchMedia(`(display-mode: ${mode})`).matches);
  const status = document.getElementById('private-install-status');
  const button = document.getElementById('private-install-button');
  let prompt;
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {
    status.textContent = 'No se pudo preparar la instalación. Comprueba tu conexión y vuelve a cargar.';
  });
  if (installed) {
    if (root.dataset.appReady === '1') {
      document.getElementById('private-install').hidden = true;
      document.getElementById('private-verification').hidden = false;
    } else {
      button.hidden = true;
      status.textContent = 'Preparando la verificación…';
      fetch(root.dataset.appUrl, {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json', 'X-CSRFToken':document.querySelector('[name=ox-csrf-token]').content},
        body:JSON.stringify({standalone:true})
      }).then(async response => {
        if (!response.ok) throw new Error('access');
        const data = await response.json(); location.replace(data.next);
      }).catch(() => {status.textContent = 'No pudimos continuar. Comprueba la conexión y vuelve a abrir la aplicación.';});
    }
    return;
  }
  window.addEventListener('beforeinstallprompt', event => {event.preventDefault(); prompt=event;});
  window.addEventListener('appinstalled', () => {
    prompt=null; button.textContent='Aplicación instalada';
    status.textContent='Abre la aplicación desde su icono para verificar tu teléfono.';
  });
  button.addEventListener('click', async () => {
    if (!prompt) {document.getElementById('private-install-help').open=true; return;}
    const pending=prompt; prompt=null;
    await pending.prompt();
    const result=await pending.userChoice;
    status.textContent=result.outcome==='accepted' ? 'Abre la aplicación desde su icono para continuar.' : 'Puedes instalarla cuando quieras desde este botón o el menú del navegador.';
  });
})();
