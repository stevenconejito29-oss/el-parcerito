(() => {
  'use strict';
  const log = document.getElementById('wcp-messages');
  const form = document.getElementById('wcp-form');
  const input = document.getElementById('wcp-input');
  const status = document.getElementById('wcp-status');
  const resume = document.getElementById('wcp-resume');
  const agent = document.getElementById('wcp-agent');
  const handoff = document.getElementById('wcp-handoff');
  const orders = document.getElementById('wcp-orders');
  const reorder = document.getElementById('wcp-reorder');
  const quick = [...document.querySelectorAll('[data-wcp-quick]')];
  if (!log || !form || !input) return;
  const csrf = document.querySelector('meta[name="ox-csrf-token"]')?.content || '';
  const submit = form.querySelector('[type="submit"]');
  const retry = document.getElementById('wcp-retry');
  const history = document.getElementById('wcp-history');
  const latest = document.getElementById('wcp-latest');
  const handoffCopy = document.getElementById('wcp-handoff-copy');
  let last = 0, busy = false, timer = null, loading = false, failures = 0;
  let pendingSend = null;
  let requests = Promise.resolve();
  let ordersSignature = null;
  let unfocusedHeight = window.visualViewport?.height || window.innerHeight;

  // iOS no siempre actualiza 100dvh al abrir el teclado. Publicamos la altura
  // visual real para que el compositor permanezca visible sin convertir esta
  // vista independiente en una modal ni desplazar al cliente al menú.
  function syncViewport() {
    const viewport = window.visualViewport;
    const height = Math.round(viewport?.height || window.innerHeight);
    const covered = Math.max(0, Math.round(window.innerHeight - height - (viewport?.offsetTop || 0)));
    if (document.activeElement !== input) unfocusedHeight = height;
    const keyboardOpen = document.activeElement === input && (covered > 120 || unfocusedHeight - height > 120);
    document.documentElement.style.setProperty('--app-height', `${height}px`);
    document.documentElement.style.setProperty('--keyboard-offset', `${Math.max(0, Math.round(viewport?.offsetTop || 0))}px`);
    document.body.classList.toggle('ox-keyboard-open', keyboardOpen);
    const nav = document.querySelector('.ox-bottom-nav');
    const reserve = !keyboardOpen && nav && getComputedStyle(nav).display !== 'none' ? Math.max(0, window.innerHeight - nav.getBoundingClientRect().top) : 0;
    document.documentElement.style.setProperty('--chat-nav-reserve', `${Math.ceil(reserve)}px`);

  }

  function call(path, body) {
    const task = requests.catch(() => {}).then(async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
    const response = await fetch(`/api/web-chat${path}`, {
      method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      signal: controller.signal,
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.error || 'No se pudo completar la acción.');
    return data;
    } finally { clearTimeout(timeout); }
    });
    requests = task;
    return task;
  }
  function renderOrders(rows = []) {
    if (!orders) return;
    const signature = JSON.stringify(rows);
    if (signature === ordersSignature) return;
    ordersSignature = signature;
    const scrollLeft = orders.scrollLeft;
    orders.replaceChildren();
    orders.hidden = rows.length === 0;
    rows.forEach(row => {
      const card = document.createElement('div'); card.className = 'wcp-order-card';
      const copy = document.createElement('div'); copy.className = 'wcp-order-copy';
      const strong = document.createElement('strong'); strong.textContent = row.number;
      const small = document.createElement('small'); small.textContent = [row.fulfillment_label, row.status_label || row.status].filter(Boolean).join(' · ');
      copy.append(strong, small); card.append(copy);
      if (row.tracking_url) {
        const link = document.createElement('a'); link.className = 'wcp-order-track';
        link.href = row.tracking_url; link.textContent = 'Ver pedido y ticket'; card.append(link);
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
    orders.scrollLeft = scrollLeft;
  }
  function renderReorder(row) {
    if (!reorder) return;
    reorder.replaceChildren(); reorder.hidden = !row;
    if (!row) return;
    const button = document.createElement('button');
    button.type = 'button'; button.dataset.reorderId = row.id;
    button.textContent = `↻ ${row.label}`; reorder.append(button);
  }
  function render(data, { older = false, follow = false } = {}) {
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
    const oldHeight = log.scrollHeight;
    const oldTop = log.scrollTop;
    const anchor = [...log.children].find(node => node.offsetTop + node.offsetHeight > log.scrollTop);
    const anchorTop = anchor?.getBoundingClientRect().top;
    let added = false;
    for (const message of data.messages || []) {
      if (log.querySelector(`[data-id="${message.id}"]`)) continue;
      const node = document.createElement('div');
      // motion-fade-in: mensaje nuevo entra con fade suave (definido en motion.css).
      node.className = `wcp-message is-${message.sender}${older ? '' : ' motion-fade-in'}`;
      node.dataset.id = message.id;
      const meta = document.createElement('div'); meta.className = 'wcp-message-meta';
      const author = document.createElement('span');
      author.textContent = ({client:'Tú',bot:'Asistente',agent:'Equipo de atención',system:'Información'})[message.sender] || 'Información';
      meta.append(author);
      const sentAt = message.created_at ? new Date(message.created_at) : null;
      if (sentAt && !Number.isNaN(sentAt.getTime())) {
        const time = document.createElement('time');
        time.dateTime = sentAt.toISOString();
        time.textContent = sentAt.toLocaleTimeString('es', {hour:'2-digit',minute:'2-digit'});
        time.title = sentAt.toLocaleString('es');
        meta.append(time);
      }
      const body = document.createElement('div'); body.className = 'wcp-message-body'; body.textContent = message.body;
      node.append(meta, body);
      const next = Array.from(log.children).find(child => Number(child.dataset.id) > message.id);
      log.insertBefore(node, next || null); last = Math.max(last, message.id || 0);
      added = true;
    }
    if (older) {
      log.scrollTop = oldTop + log.scrollHeight - oldHeight;
      if (history) history.hidden = !data.has_older;
      return;
    }
    if (history && !history.dataset.initialized) {
      history.hidden = !data.has_older;
      history.dataset.initialized = '1';
    }
    if (data.orders) renderOrders(data.orders);
    if (Object.prototype.hasOwnProperty.call(data, 'reorder')) renderReorder(data.reorder);
    const state = data.conversation?.status || 'bot';
    const recognised = data.conversation?.customer_recognised;
    status.textContent = ({bot:recognised?'Asistente · pedidos de este dispositivo':'Asistente disponible',waiting_agent:'Esperando a una persona',active_agent:`Te atiende ${data.conversation?.assigned_agent || 'nuestro equipo'}`,closed:'Conversación finalizada'})[state] || 'Conectando…';
    const bot = state === 'bot';
    resume.hidden = bot;
    agent.hidden = !bot;
    if (handoff) {
      if (state !== 'bot') handoff.hidden = false;
      else if (data.offer_human === true) handoff.hidden = false;
      else if (data.offer_human === false) handoff.hidden = true;
    }
    if (handoffCopy) handoffCopy.textContent = ({
      bot: 'Puedes pedir que una persona continúe este chat.',
      waiting_agent: 'Tu solicitud está en cola. Puedes seguir escribiendo; el equipo leerá los mensajes.',
      active_agent: 'Una persona del equipo está atendiendo tu consulta.',
      closed: 'El chat terminó. Vuelve al asistente para una nueva consulta.',
    })[state] || '';
    quick.forEach(button => { button.disabled = !bot || busy || Boolean(pendingSend); });
    input.disabled = state === 'closed';
    input.placeholder = state === 'waiting_agent' ? 'Añade información para el equipo…' : 'Escribe tu pregunta…';
    if (atBottom || follow) log.scrollTop = log.scrollHeight;
    else {
      if (anchor && anchor.isConnected) log.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
      if (added && latest) latest.hidden = false;
    }
  }
  function schedule(delay = 3000) {
    clearTimeout(timer);
    if (!document.hidden) timer = setTimeout(load, delay);
  }
  async function load() {
    if (loading || busy || document.hidden) { schedule(); return; }
    loading = true;
    let more = false;
    try {
      const data = await call(last ? `/state?after=${last}` : '/state');
      render(data); failures = 0; more = data.has_more;
    } catch (_) {
      failures += 1;
      status.textContent = navigator.onLine === false ? 'Sin conexión. Conservamos tu mensaje.' : 'Reconectando…';
    } finally { loading = false; schedule(more ? 0 : Math.min(30000, 3000 * 2 ** Math.min(failures, 4))); }
  }
  async function send(text, isRetry = false) {
    if (!text || busy || input.disabled) return;
    if (pendingSend && !isRetry) return;
    pendingSend ||= {message:text, nonce:crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`};
    busy = true;
    submit.disabled = true; form.setAttribute('aria-busy', 'true');
    if (retry) retry.hidden = true;
    quick.forEach(button => { button.disabled = true; });
    try {
      render(await call('/messages', pendingSend), {follow:true});
      pendingSend = null;
    } catch (_) {
      status.textContent = 'No pudimos confirmar el envío. Reintenta sin duplicar el mensaje.';
      if (retry) retry.hidden = false;
    } finally {
      busy = false; submit.disabled = Boolean(pendingSend);
      form.setAttribute('aria-busy', 'false');
      quick.forEach(button => { button.disabled = !agent || agent.hidden || Boolean(pendingSend); });
      if (matchMedia('(pointer:fine)').matches) input.focus({preventScroll:true});
    }
  }
  form.addEventListener('submit', event => { event.preventDefault(); event.stopPropagation(); const text=input.value.trim(); if(!text||busy||pendingSend||input.disabled)return; input.value=''; send(text); });
  retry?.addEventListener('click', () => pendingSend && send(pendingSend.message, true));
  history?.addEventListener('click', async () => {
    history.disabled = true;
    try { render(await call(`/state?before=${log.firstElementChild?.dataset.id || 0}`), {older:true}); }
    catch (_) { status.textContent = 'No se pudo cargar el historial. Puedes reintentarlo.'; }
    finally { history.disabled = false; }
  });
  latest?.addEventListener('click', () => { log.scrollTop = log.scrollHeight; latest.hidden = true; });
  log.addEventListener('scroll', () => { if (latest && log.scrollHeight - log.scrollTop - log.clientHeight < 60) latest.hidden = true; }, {passive:true});
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
  agent.addEventListener('click', () => requireSecondTap(agent, 'Toca otra vez para confirmar', async () => { try { render(await call('/request-agent', {})); } catch (error) { status.textContent=error.message; } }));
  resume.addEventListener('click', () => requireSecondTap(resume, 'Confirmar vuelta al asistente', async () => { try { render(await call('/resume-bot', {})); input.disabled=false; } catch(error){status.textContent=error.message;} }));
  async function handleOrderAction(event) {
    const repeat=event.target.closest('[data-reorder-id]');
    if(repeat){repeat.disabled=true;try{const data=await call(`/orders/${repeat.dataset.reorderId}/reorder`,{});window.location.assign(data.redirect_url||'/carrito');}catch(error){status.textContent=error.message;repeat.disabled=false;}return;}
    const button=event.target.closest('[data-order-id]'); if(!button)return;
    const number=button.dataset.orderNumber || 'seleccionado';
    if(!window.confirm(`¿Cancelar definitivamente el pedido ${number}?`))return;
    button.disabled=true;
    try{render(await call(`/orders/${button.dataset.orderId}/cancel`,{confirm:true}));}catch(error){status.textContent=error.message;button.disabled=false;}
  }
  orders?.addEventListener('click', handleOrderAction);
  reorder?.addEventListener('click', handleOrderAction);
  syncViewport();
  window.visualViewport?.addEventListener('resize', syncViewport, {passive:true});
  window.visualViewport?.addEventListener('scroll', syncViewport, {passive:true});
  window.addEventListener('orientationchange', syncViewport, {passive:true});
  window.addEventListener('resize', syncViewport, {passive:true});
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
  load();
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)load();});
  window.addEventListener('online', () => schedule(0));
  window.addEventListener('pagehide', () => clearTimeout(timer));
})();
