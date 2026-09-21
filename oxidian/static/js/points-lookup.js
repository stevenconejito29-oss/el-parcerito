(() => {
  const form = document.getElementById('points-lookup-form');
  if (!form) return;
  const phone = form.querySelector('#points-phone'), code = form.querySelector('#points-code');
  const status = form.querySelector('#points-lookup-status'), step = form.querySelector('[data-points-code-step]');
  const balance = form.querySelector('[data-points-balance]'), requestButton = form.querySelector('[data-points-request]');
  let busy = false, revision = 0, sentPhone = '', nextSend = 0;
  const canonical = () => phone.value.replace(/\D/g, '');
  const setBusy = value => { busy = value; form.querySelectorAll('button').forEach(button => { button.disabled = value; }); };
  async function action(verify = false) {
    if (busy) return;
    const number = canonical(), generation = revision;
    if (number.length < 7) { status.textContent = 'Escribe tu número de WhatsApp.'; phone.focus(); return; }
    if (verify && (number !== sentPhone || !/^\d{6}$/.test(code.value.trim()))) { status.textContent = 'Solicita un código para este teléfono e introduce sus seis dígitos.'; return; }
    if (!verify && number === sentPhone && Date.now() < nextSend) { status.textContent = `Espera ${Math.ceil((nextSend - Date.now()) / 1000)}s antes de reenviar.`; return; }
    setBusy(true);
    status.textContent = verify ? 'Verificando…' : 'Solicitando código…';
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(verify ? '/puntos/verificar-saldo' : '/puntos/solicitar-codigo', {
        method: 'POST', signal: controller.signal,
        headers: {'Content-Type':'application/json','X-CSRFToken':form.querySelector('[name=csrf_token]').value},
        body: JSON.stringify({telefono:number,codigo:verify ? code.value.trim() : undefined}),
      });
      const data = await response.json();
      if (generation !== revision) return;
      if (!response.ok || !data.ok) throw new Error(data.msg || 'No pudimos completar la consulta.');
      if (verify) {
        form.querySelector('[data-points-value]').textContent = String(data.puntos);
        balance.hidden = false; step.hidden = true;
        status.textContent = 'Saldo verificado. Tus puntos se consumen únicamente al confirmar un canje.';
      } else {
        sentPhone = number; nextSend = Date.now() + Math.max(0, Number(data.resend_seconds) || 0) * 1000;
        step.hidden = false; requestButton.hidden = true;
        status.textContent = data.msg || 'Si el teléfono está registrado, recibirá un código.';
        code.focus();
      }
    } catch (error) {
      if (generation === revision) status.textContent = error.name === 'AbortError' ? 'La conexión tarda demasiado. Revisa tu conexión y vuelve a intentarlo.' : error.message;
    } finally { clearTimeout(timeout); setBusy(false); }
  }
  form.addEventListener('submit', event => { event.preventDefault(); action(!step.hidden); });
  form.querySelector('[data-points-verify]').addEventListener('click', () => action(true));
  form.querySelector('[data-points-resend]').addEventListener('click', () => action(false));
  phone.addEventListener('input', () => { revision++; balance.hidden = true; step.hidden = true; requestButton.hidden = false; code.value = ''; status.textContent = ''; });
})();
