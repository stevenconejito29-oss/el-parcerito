/* Edición del carrito; Flask valida cantidades, disponibilidad y precios. */
(function () {
'use strict';

const cartForm = document.getElementById('cr-form-qty');
const CR_MAX = Number(cartForm?.dataset.maxQuantity || 1);
let cartDirty = false;

/* ── Cambiar cantidad (por line_key: cada línea = fila propia en carrito) ── */
function crQty(lineKey, delta) {
  if (!lineKey) return;
  const inp = document.getElementById('qty_' + lineKey);
  if (!inp) return;
  let v = (Number.parseInt(inp.value, 10) || 0) + delta;
  v = Math.max(0, Math.min(CR_MAX, v));
  inp.value = v;
  crShowUpdate();
  crRecalc();

}

document.addEventListener('click', function(event) {
  const quantity = event.target.closest('[data-cart-qty]');
  if (quantity) {
    // Prioriza line-key (modelo nuevo); cae a product-id para líneas legacy.
    const lineKey = quantity.dataset.cartLineKey || quantity.dataset.cartProductId;
    crQty(String(lineKey), Number.parseInt(quantity.dataset.cartQty, 10));
    return;
  }
  if (event.target.closest('[data-cart-update]')) {
    document.getElementById('cr-continuar').value = '';
    cartForm?.requestSubmit(); return;
  }
});

function crShowUpdate() {
  cartDirty = true;
  const r = document.getElementById('cr-update-row');
  if (r) r.hidden = false;
}

function crRecalc() {
  if (cartForm && !cartForm.checkValidity()) return;
  let sub = 0;
  document.querySelectorAll('.cr-item').forEach(el => {
    const key   = el.dataset.lineKey || el.dataset.productId;
    const price = parseFloat(el.dataset.price || 0);
    const qty   = Number.parseInt(document.getElementById('qty_' + key)?.value || 0, 10);
    if (!Number.isFinite(qty) || qty < 0 || qty > CR_MAX) return;
    const st    = Math.round(price * qty * 100) / 100;
    const stEl  = document.getElementById('subtotal_' + key);
    if (stEl) { stEl.textContent = '€' + st.toFixed(2); crPulse(stEl); }
    sub += st;
  });
  const td = document.getElementById('total-display');
  const te = document.getElementById('total-estimado');
  if (td) { td.textContent = '€' + sub.toFixed(2); crPulse(td); }
  if (te) { te.textContent = '€' + sub.toFixed(2); crPulse(te); }
}

function crPulse(el) {
  if (!el) return;
  el.classList.remove('cr-pulse');
  void el.offsetWidth;
  el.classList.add('cr-pulse');
}

/* ── Qty input change ── */
document.querySelectorAll('.cr-qty-input').forEach(inp => {
  inp.addEventListener('input', () => { crShowUpdate(); crRecalc();

  });
});

/* ── Eliminar item con confirmación robusta ─────────────────────────────
   Reemplaza el `onclick=` inline anterior que tenía un pattern con
   `this.click()` recursivo. Aquí:
     • el listener intercepta el submit del form padre
     • deshabilita el botón mientras el toast está abierto
     • si el usuario cancela, restaura el estado normal
     • si confirma, submit del form (SIN dispatchear un segundo click)
   Robusto ante toques dobles y sin recursión.
──────────────────────────────────────────────────────────────────────── */
document.addEventListener('click', function (ev) {
  const btn = ev.target.closest('.js-cart-remove');
  if (!btn || btn.dataset.crConfirmed === '1' || btn.disabled) return;
  ev.preventDefault();
  const form = btn.form;
  const msg = btn.dataset.confirm || `¿Quitar de tu ${OX_UI.cart_name || 'canasta'}?`;
  btn.disabled = true;
  const ask = window.OxToast?.confirm
    ? window.OxToast.confirm({
        title: 'Quitar producto', body: msg,
        confirmText: 'Sí, quitar', cancelText: 'Cancelar', danger: true, icon: 'canasto',
      })
    : Promise.resolve(confirm(msg));
  Promise.resolve(ask).then(function (ok) {
    if (!ok) { btn.disabled = false; return; }
    btn.dataset.crConfirmed = '1';
    // Copiamos el line_key del botón al hidden input dedicado. Sin esto
    // iOS Safari con `form.submit()` (fallback) omite el name/value del
    // submitter y el backend recibía line_key='' — el producto no se
    // eliminaba (bug rastreado en logs para productos de socios y
    // cualquier línea con firma compleja).
    const lineKey = btn.getAttribute('data-line-key') || '';
    const hidden = document.getElementById('cr-line-key-to-delete');
    if (hidden) hidden.value = lineKey;
    if (form && typeof form.requestSubmit === 'function') {
      // requestSubmit respeta el formaction del botón que disparó el submit.
      form.requestSubmit(btn);
    } else if (form) {
      form.action = btn.getAttribute('formaction') || form.action;
      form.submit();
    }
  }).catch(function () { btn.disabled = false; });
});


// Guarda las cantidades antes de abrir el checkout, sin temporizadores que
// recarguen la pantalla mientras se edita otra línea.
document.getElementById('btn-checkout')?.addEventListener('click', event => {
  if (!cartDirty || !cartForm) return;
  event.preventDefault();
  const continuation = document.getElementById('cr-continuar');
  if (continuation) continuation.value = 'checkout';
  cartForm.requestSubmit();
});
cartForm?.addEventListener('submit', () => {
  cartForm.setAttribute('aria-busy', 'true');
});

})();
