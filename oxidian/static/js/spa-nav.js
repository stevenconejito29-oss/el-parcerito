/* ═══════════════════════════════════════════════════════════════════
   spa-nav.js — Navegación SPA-lite con View Transitions
   Intercepta clics a rutas internas, hace fetch del HTML, extrae <main>
   y reemplaza en el DOM actual dentro de startViewTransition() para
   obtener una transición nativa suave sin flash blanco ni reflow global.
   Fallback: navegación normal cuando el browser no soporta VT o hay error.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  if (typeof window === 'undefined') return;
  const supports = 'startViewTransition' in document;
  if (!supports) return;

  const CACHE = new Map();
  const CACHE_TTL = 30_000;
  const parser = new DOMParser();

  function sameOrigin(url) {
    try { return new URL(url, location.href).origin === location.origin; }
    catch { return false; }
  }

  function shouldIntercept(a, ev) {
    if (!a || !a.href) return false;
    if (ev.defaultPrevented) return false;
    if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return false;
    if (a.target && a.target !== '_self') return false;
    if (a.hasAttribute('download')) return false;
    if (a.dataset.noSpa === '' || a.dataset.noSpa === '1' || a.dataset.noSpa === 'true') return false;
    if (a.getAttribute('href')?.startsWith('#')) return false;
    if (!sameOrigin(a.href)) return false;
    const url = new URL(a.href, location.href);
    if (url.pathname === location.pathname && url.search === location.search) return false;
    // Rutas que rompen el swap: descargas, admin heavy, api
    if (/^\/(api|admin|superadmin|preparador|repartidor|auth|webhook)\b/.test(url.pathname)) return false;
    return true;
  }

  async function fetchPage(url) {
    const now = Date.now();
    const cached = CACHE.get(url);
    if (cached && (now - cached.at) < CACHE_TTL) return cached.doc;
    const res = await fetch(url, { headers: { 'X-SPA-Nav': '1', 'Accept': 'text/html' }, credentials: 'same-origin' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const html = await res.text();
    const doc = parser.parseFromString(html, 'text/html');
    CACHE.set(url, { doc, at: now });
    return doc;
  }

  function swap(doc, url) {
    const newMain = doc.querySelector('main');
    const oldMain = document.querySelector('main');
    if (!newMain || !oldMain) throw new Error('No <main>');
    oldMain.replaceWith(newMain);
    // Titulo + head selectivo (no reemplazamos <head> entero para no revalidar CSS/JS)
    const newTitle = doc.querySelector('title')?.textContent;
    if (newTitle) document.title = newTitle;
    // Ejecuta <script> inline dentro del nuevo main (contiene EP_DATA y filtros de home)
    newMain.querySelectorAll('script').forEach(s => {
      if (s.src) return; // externos ya se cargaron via base.html
      const clone = document.createElement('script');
      if (s.type) clone.type = s.type;
      clone.textContent = s.textContent;
      s.replaceWith(clone);
    });
    // Reset scroll
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    // Dispatch para que módulos externos re-inicien (carrito, header, etc.)
    document.dispatchEvent(new CustomEvent('spa:navigated', { detail: { url } }));
  }

  async function navigate(url) {
    document.body.classList.add('is-navigating');
    try {
      const doc = await fetchPage(url);
      const transition = document.startViewTransition(() => swap(doc, url));
      await transition.finished.catch(() => {});
      history.pushState({ spa: true }, '', url);
    } catch (err) {
      console.warn('[spa-nav] fallback', err);
      location.assign(url);
    } finally {
      document.body.classList.remove('is-navigating');
    }
  }

  document.addEventListener('click', (ev) => {
    const a = ev.target.closest('a[href]');
    if (!shouldIntercept(a, ev)) return;
    ev.preventDefault();
    navigate(a.href);
  }, true);

  // Prefetch on hover/focus para calentar cache antes del clic
  const prefetched = new Set();
  function prefetch(url) {
    if (prefetched.has(url)) return;
    prefetched.add(url);
    fetchPage(url).catch(() => prefetched.delete(url));
  }
  document.addEventListener('mouseover', (ev) => {
    const a = ev.target.closest('a[href]');
    if (a && sameOrigin(a.href) && !/^\/(api|admin|superadmin|auth|webhook)\b/.test(new URL(a.href).pathname)) {
      prefetch(a.href);
    }
  }, { passive: true });
  document.addEventListener('touchstart', (ev) => {
    const a = ev.target.closest('a[href]');
    if (a && sameOrigin(a.href)) prefetch(a.href);
  }, { passive: true });

  window.addEventListener('popstate', () => {
    // Si venimos de historia SPA, refetch + swap (mantiene VT)
    navigate(location.href);
  });
})();
