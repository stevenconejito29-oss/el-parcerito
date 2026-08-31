(() => {
  const shell = document.getElementById('web-chat');
  const panel = shell?.querySelector('.wc-panel');
  const log = document.getElementById('wc-messages');
  const form = document.getElementById('wc-form');
  const input = document.getElementById('wc-input');
  const status = document.getElementById('wc-status');
  const resume = document.getElementById('wc-resume');
  if (!shell || !panel || !log || !form || !input || !status || !resume) return;

  const csrf = document.querySelector('meta[name="ox-csrf-token"]')?.content || '';
  const headers = {'Content-Type': 'application/json', 'X-CSRFToken': csrf};
  const openers = [...document.querySelectorAll('[data-web-chat-open]')];
  let last = 0, poll = null, busy = false, returnFocus = null;
  const resizeInput = () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 112)}px`; };
  const syncViewport = () => {
    const height = window.visualViewport?.height || window.innerHeight;
    document.documentElement.style.setProperty('--app-height', `${height}px`);
    if (!shell.hidden) log.scrollTop = log.scrollHeight;
  };
  const render = (data) => {
    if (!data?.ok) return;
    for (const message of data.messages || []) {
      if (log.querySelector(`[data-wc-id="${message.id}"]`)) continue;
      const element = document.createElement('div');
      element.className = `wc-message is-${message.sender} motion-fade-in`;
      element.dataset.wcId = message.id;
      element.textContent = message.body;
      log.append(element);
      last = Math.max(last, message.id || 0);
    }
    const state = data.conversation?.status || 'bot';
    const labels = {bot:'Asistente disponible', waiting_agent:'En cola para atención', active_agent:`Te atiende ${data.conversation?.assigned_agent || 'nuestro equipo'}`, closed:'Conversación cerrada'};
    status.textContent = labels[state] || 'Conectando…';
    resume.hidden = state === 'bot';
    input.disabled = state === 'waiting_agent' || state === 'closed';
    input.placeholder = state === 'waiting_agent' ? 'Un agente tomará el chat…' : 'Escribe tu pregunta…';
    log.scrollTop = log.scrollHeight;
  };
  const call = async (path, body) => {
    const response = await fetch(`/api/web-chat${path}`, {method:body?'POST':'GET', headers, body:body?JSON.stringify(body):undefined, credentials:'same-origin'});
    if (!response.ok) throw new Error('request');
    return response.json();
  };
  const load = async () => { try { render(await call(`/state?after=${last}`)); } catch (_) { status.textContent = 'Reconectando…'; } };
  const startPolling = () => { if (poll) clearInterval(poll); poll = window.setInterval(load, 3000); };
  const open = (event) => {
    if (!shell.hidden) return;
    returnFocus = event?.currentTarget || document.activeElement;
    shell.hidden = false;
    document.body.classList.add('ox-modal-open');
    openers.forEach((button) => button.setAttribute('aria-expanded', 'true'));
    requestAnimationFrame(() => shell.classList.add('is-open'));
    syncViewport(); load(); startPolling(); window.setTimeout(() => input.focus({preventScroll:true}), 180);
  };
  const close = () => {
    if (shell.hidden) return;
    shell.classList.remove('is-open'); document.body.classList.remove('ox-modal-open');
    openers.forEach((button) => button.setAttribute('aria-expanded', 'false'));
    if (poll) clearInterval(poll); poll = null;
    window.setTimeout(() => { shell.hidden = true; returnFocus?.focus?.(); }, 180);
  };
  openers.forEach((button) => { button.setAttribute('aria-controls','web-chat'); button.setAttribute('aria-expanded','false'); button.addEventListener('click',open); });
  shell.querySelectorAll('[data-web-chat-close]').forEach((button) => button.addEventListener('click', close));
  shell.querySelectorAll('[data-wc-quick]').forEach((button) => button.addEventListener('click', () => { input.value=button.dataset.wcQuick; resizeInput(); form.requestSubmit(); }));
  shell.querySelector('[data-wc-agent]')?.addEventListener('click', async () => { try { render(await call('/request-agent', {})); } catch (_) { status.textContent='No pudimos solicitar un agente'; } });
  resume.addEventListener('click', async () => { try { render(await call('/resume-bot', {})); input.disabled=false; input.focus(); } catch (_) { status.textContent='No se pudo volver al asistente'; } });
  input.addEventListener('input', resizeInput);
  input.addEventListener('keydown', (event) => { if (event.key==='Enter'&&!event.shiftKey&&!event.isComposing) { event.preventDefault(); form.requestSubmit(); } });
  form.addEventListener('submit', async (event) => {
    event.preventDefault(); event.stopPropagation(); const message=input.value.trim(); if(!message||busy||input.disabled)return;
    busy=true; input.value=''; resizeInput();
    try { const nonce=crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`; render(await call('/messages',{message,nonce})); }
    catch (_) { input.value=message; resizeInput(); status.textContent='No se pudo enviar. Inténtalo otra vez.'; }
    finally { busy=false; }
  });
  document.addEventListener('keydown', (event) => {
    if(event.key==='Escape'&&!shell.hidden)close();
    if(event.key==='Tab'&&!shell.hidden){const focusable=[...panel.querySelectorAll('button:not([hidden]):not(:disabled),textarea:not(:disabled)')];if(!focusable.length)return;const first=focusable[0],final=focusable.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();final.focus();}else if(!event.shiftKey&&document.activeElement===final){event.preventDefault();first.focus();}}
  });
  document.addEventListener('visibilitychange',()=>{if(shell.hidden)return;if(document.hidden&&poll){clearInterval(poll);poll=null;}else if(!document.hidden){load();startPolling();}});
  window.visualViewport?.addEventListener('resize', syncViewport, {passive:true});
  window.addEventListener('orientationchange', syncViewport, {passive:true});
  if(location.hash==='#chat')open();
})();
