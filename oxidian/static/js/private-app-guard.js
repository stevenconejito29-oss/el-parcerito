/* UX de app privada; el servidor comprueba autorización y OTP en cada petición. */
(() => {
  const app = navigator.standalone === true || ['standalone','fullscreen','minimal-ui'].some(mode => matchMedia(`(display-mode: ${mode})`).matches);
  if (app) document.getElementById('private-app-veil')?.remove();
  else location.replace('/acceso?instalar=1');
})();
