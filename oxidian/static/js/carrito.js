// carrito.js — helpers mínimos para el carrito
// El estado principal vive en la sesión Flask; este archivo
// maneja feedback visual en el cliente.

document.addEventListener('DOMContentLoaded', () => {
  // Confirmaciones de eliminación inline
  document.querySelectorAll('[data-confirm]').forEach(btn => {
    btn.addEventListener('click', e => {
      if (!confirm(btn.dataset.confirm)) e.preventDefault();
    });
  });
});
