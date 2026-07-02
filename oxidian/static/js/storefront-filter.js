/* storefront-filter.js
 * Filtrado client-side de categorías en el index público. Evita la recarga
 * al cambiar de chip y respeta las secciones del catálogo (combos / resto).
 *
 * Contrato del HTML existente:
 *   .ep-cats a[data-category]    chips ("" = todos, "<id>" = filtrar por id)
 *   .ep-card[data-category-id]   tarjetas dentro de .ep-grid
 *   .ep-cat-section              wrapper de cada sección (combos, resto, etc.)
 *
 * Si JS falla, los chips siguen siendo <a href=...> normales.
 */
(function () {
  'use strict';

  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  ready(function () {
    var catsBar = document.querySelector('.ep-cats');
    var catalog = document.getElementById('catalogo');
    if (!catsBar || !catalog) return;

    var chips = catsBar.querySelectorAll('a[data-category]');
    if (!chips.length) return;

    function currentCategoryFromUrl() {
      var m = window.location.search.match(/[?&]categoria=(\d+)/);
      return m ? m[1] : '';
    }

    // Si el server ya filtró (entrada directa a ?categoria=X o búsqueda
    // q=...), el catálogo NO contiene todas las cards. Filtrar client-side
    // mostraría parcial y confundiría. En ese caso dejamos que los chips
    // funcionen como links normales (recarga).
    if (currentCategoryFromUrl() || /[?&]q=/.test(window.location.search)) {
      return;
    }

    function applyFilter(catId, opts) {
      opts = opts || {};
      var cards = catalog.querySelectorAll('.ep-card[data-category-id]');
      cards.forEach(function (card) {
        if (!catId) {
          card.classList.remove('is-hidden');
        } else {
          var match = String(card.dataset.categoryId) === String(catId);
          card.classList.toggle('is-hidden', !match);
        }
      });
      catalog.querySelectorAll('.ep-cat-section').forEach(function (sec) {
        var anyVisible = sec.querySelector('.ep-card:not(.is-hidden)');
        sec.classList.toggle('is-empty', !anyVisible);
      });
      chips.forEach(function (chip) {
        var on = String(chip.dataset.category || '') === String(catId || '');
        chip.classList.toggle('ep-cat-on', on);
      });
      var anyCard = catalog.querySelector('.ep-card:not(.is-hidden)');
      var empty = catalog.querySelector('[data-storefront-empty]');
      if (empty) empty.hidden = !!anyCard;
      if (opts.scroll) {
        catalog.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }

    function buildUrl(catId) {
      var p = new URLSearchParams(window.location.search);
      if (catId) p.set('categoria', catId);
      else p.delete('categoria');
      var q = p.toString();
      return window.location.pathname + (q ? '?' + q : '');
    }

    applyFilter(currentCategoryFromUrl(), { scroll: false });

    chips.forEach(function (chip) {
      chip.addEventListener('click', function (e) {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
        e.preventDefault();
        var catId = chip.dataset.category || '';
        try { window.history.pushState({ catId: catId }, '', buildUrl(catId)); }
        catch (_) {}
        applyFilter(catId, { scroll: true });
      });
    });

    window.addEventListener('popstate', function (e) {
      var catId = (e.state && e.state.catId) || currentCategoryFromUrl();
      applyFilter(catId, { scroll: false });
    });
  });
})();
