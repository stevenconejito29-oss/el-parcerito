(() => {
  'use strict';
  const log = document.getElementById('wcp-messages');
  const form = document.getElementById('wcp-form');
  const input = document.getElementById('wcp-input');
  const status = document.getElementById('wcp-status');
  const resume = document.getElementById('wcp-resume');
  const agent = document.getElementById('wcp-agent');
  const orders = document.getElementById('wcp-orders');
  const quick = [...document.querySelectorAll('[data-wcp-quick]')];
  if (!log || !form || !input) return;
  const csrf = document.querySelector('meta[name="ox-csrf-token"]')?.content || '';
  let last = 0, busy = false, timer = null;

  // iOS no siempre actualiza 100dvh al abrir el teclado. Publicamos la altura
  // visual real para que el compositor permanezca visible sin convertir esta
  // vista independiente en una modal ni desplazar al cliente al menú.
  function syncViewport() {
    const viewport = window.visualViewport;
    const height = Math.round(viewport?.height || window.innerHeight);
    const covered = Math.max(0, Math.round(window.innerHeight - height - (viewport?.offsetTop || 0)));
    const keyboardOpen = document.activeElement === input && covered > 120;
    document.documentElement.style.setProperty('--app-height', `${height}px`);
    document.documentElement.style.setProperty('--keyboard-offset', `${Math.max(0, Math.round(viewport?.offsetTop || 0))}px`);
    document.body.classList.toggle('ox-keyboard-open', keyboardOpen);
    if (document.documentElement.classList.contains('ox-pwa-runtime') && window.scrollY) {
      window.scrollTo({top:0, left:0, behavior:'instant'});
    }
  }

  async function call(path, body) {
    const response = await fetch(`/api/web-chat${path}`, {
      method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.error || 'No se pudo completar la acción.');
    return data;
  }
  function renderOrders(rows = []) {
    if (!orders) return;
    orders.replaceChildren();
    orders.hidden = rows.length === 0;
    rows.forEach(row => {
      const card = document.createElement('div'); card.className = 'wcp-order-card';
      const copy = document.createElement('div'); copy.className = 'wcp-order-copy';
      const strong = document.createElement('strong'); strong.textContent = row.number;
      const small = document.createElement('small'); small.textContent = row.status_label || row.status;
      copy.append(strong, small); card.append(copy);
      if (row.tracking_url) {
        const link = document.createElement('a'); link.className = 'wcp-order-track';
        link.href = row.tracking_url; link.textContent = 'Ver estado'; card.append(link);
      }
      if (row.cancelable) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'wcp-order-cancel';
        button.dataset.orderId = row.id; button.dataset.orderNumber = row.number;
        button.textContent = 'Cancelar'; card.append(button);
      }
      if (row.reorderable) {
        const repeat = document.createElement('button');
        repeat.type = 'button'; repeat.className = 'wcp-order-repeat';
        repeat.dataset.reorderId = row.id; repeat.textContent = 'Repetir compra';
        card.append(repeat);
      }
      orders.append(card);
    });
  }
  function render(data) {
    for (const message of data.messages || []) {
      if (log.querySelector(`[data-id="${message.id}"]`)) continue;
      const node = document.createElement('div');
      // motion-fade-in: mensaje nuevo entra con fade suave (definido en motion.css).
      node.className = `wcp-message is-${message.sender} motion-fade-in`;
      node.dataset.id = message.id; node.textContent = message.body;
      log.append(node); last = Math.max(last, message.id || 0);
    }
    if (data.orders) renderOrders(data.orders);
    const state = data.conversation?.status || 'bot';
    const recognised = data.conversation?.customer_recognised;
    status.textContent = ({bot:recognised?'Asistente · pedidos de este dispositivo':'Asistente disponible',waiting_agent:'Esperando a una persona',active_agent:`Te atiende ${data.conversation?.assigned_agent || 'nuestro equipo'}`,closed:'Conversación finalizada'})[state] || 'Conectando…';
    const bot = state === 'bot'; resume.hidden = bot; agent.hidden = !bot;
    quick.forEach(button => { button.disabled = !bot; });
    input.disabled = state === 'waiting_agent' || state === 'closed';
    input.placeholder = state === 'waiting_agent' ? 'Puedes volver al asistente cuando quieras…' : 'Escribe tu pregunta…';
    log.scrollTop = log.scrollHeight;
  }
  async function load() { try { render(await call(`/state?after=${last}`)); } catch (_) { status.textContent = 'Reconectando…'; } }
  async function send(text) {
    if (!text || busy || input.disabled) return;
    busy = true;
    try { render(await call('/messages', {message:text, nonce:crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`})); }
    catch (error) { input.value = text; status.textContent = error.message; }
    finally { busy = false; input.setAttribute('aria-busy', 'false'); if (matchMedia('(pointer:fine)').matches) input.focus({preventScroll:true}); }
  }
  form.addEventListener('submit', event => { event.preventDefault(); event.stopPropagation(); const text=input.value.trim(); if(!text)return; input.value=''; input.setAttribute('aria-busy','true'); send(text); });
  input.addEventListener('keydown', event => { if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();form.requestSubmit();} });
  quick.forEach(button => button.addEventListener('click', () => send(button.dataset.wcpQuick)));
  const confirmTimers = new WeakMap();
  function requireSecondTap(button, confirmation, action) {
    if (!button.classList.contains('is-confirming')) {
      button.classList.add('is-confirming');
      button.dataset.originalLabel = button.textContent;
      button.textContent = confirmation;
      clearTimeout(confirmTimers.get(button));
      confirmTimers.set(button, setTimeout(() => {
        button.classList.remove('is-confirming');
        button.textContent = button.dataset.originalLabel || '';
      }, 4200));
      return;
    }
    clearTimeout(confirmTimers.get(button));
    button.classList.remove('is-confirming');
    action();
  }
  agent.addEventListener('click', () => requireSecondTap(agent, 'Confirmar atención humana', async () => { try { render(await call('/request-agent', {})); } catch (error) { status.textContent=error.message; } }));
  resume.addEventListener('click', () => requireSecondTap(resume, 'Confirmar vuelta al asistente', async () => { try { render(await call('/resume-bot', {})); input.disabled=false; } catch(error){status.textContent=error.message;} }));
  orders?.addEventListener('click', async event => {
    const repeat=event.target.closest('[data-reorder-id]');
    if(repeat){repeat.disabled=true;try{const data=await call(`/orders/${repeat.dataset.reorderId}/reorder`,{});window.location.assign(data.redirect_url||'/carrito');}catch(error){status.textContent=error.message;repeat.disabled=false;}return;}
    const button=event.target.closest('[data-order-id]'); if(!button)return;
    const number=button.dataset.orderNumber || 'seleccionado';
    if(!window.confirm(`¿Cancelar definitivamente el pedido ${number}?`))return;
    button.disabled=true;
    try{render(await call(`/orders/${button.dataset.orderId}/cancel`,{confirm:true}));}catch(error){status.textContent=error.message;button.disabled=false;}
  });
  syncViewport();
  window.visualViewport?.addEventListener('resize', syncViewport, {passive:true});
  window.visualViewport?.addEventListener('scroll', syncViewport, {passive:true});
  window.addEventListener('orientationchange', syncViewport, {passive:true});
  input.addEventListener('focus', () => {
    // Safari publica la altura final del teclado en varios fotogramas.
    // Recalcular evita que el compositor quede anclado al viewport de layout.
    [0, 80, 220, 420].forEach(delay => window.setTimeout(syncViewport, delay));
  });
  input.addEventListener('blur', () => {
    window.setTimeout(() => {
      document.body.classList.remove('ox-keyboard-open');
      syncViewport();
    }, 320);
  });
  window.addEventListener('pageshow', () => {
    document.body.classList.remove('ox-keyboard-open');
    document.documentElement.style.removeProperty('--keyboard-offset');
    window.setTimeout(syncViewport, 0);
    window.setTimeout(syncViewport, 280);
  });
  load(); timer=setInterval(load,3000);
  document.addEventListener('visibilitychange',()=>{clearInterval(timer);if(!document.hidden){load();timer=setInterval(load,3000);}});
})();
