/* Filtra únicamente los enlaces ya autorizados y renderizados por el servidor. */
(() => {
  'use strict';
  const search = document.querySelector('[data-tool-search]');
  const nav = document.getElementById('sb-nav');
  if (!search || !nav) return;
  const input = search.querySelector('input');
  const status = document.getElementById('ox-tool-results');
  const links = [...nav.querySelectorAll('a.ox-sb-item')];
  const groups = [...nav.querySelectorAll('.ox-sb-category')];
  const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es').trim();
  const labels = new Map(links.map(link => [link, normalize(link.textContent)]));
  let savedOpen = null;
  // No mostrar categorías vacías por los permisos del usuario.
  const empty = new Set(groups.filter(group => !group.querySelector('a.ox-sb-item')));
  empty.forEach(group => { group.hidden = true; });
  function filter() {
    const query = normalize(input.value);
    const words = query.split(/\s+/).filter(Boolean);
    if (query && !savedOpen) savedOpen = new Map(groups.map(group => [group, group.open]));
    let count = 0;
    links.forEach(link => {
      const group = link.closest('.ox-sb-category');
      const label = labels.get(link) + ' ' + normalize(group?.querySelector('summary')?.textContent || '');
      link.hidden = !words.every(word => label.includes(word));
      if (!link.hidden) count++;
    });
    groups.forEach(group => {
      group.hidden = empty.has(group) || ![...group.querySelectorAll('a.ox-sb-item')].some(link => !link.hidden);
      if (query) group.open = !group.hidden;
      else if (savedOpen) group.open = savedOpen.get(group);
    });
    if (!query) savedOpen = null;
    nav.classList.toggle('is-filtering', Boolean(query));
    status.textContent = query ? (count ? `${count} acceso${count === 1 ? '' : 's'} encontrado${count === 1 ? '' : 's'}` : 'No hay herramientas con ese nombre.') : '';
  }
  input.addEventListener('input', filter);
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape' && input.value) {
      event.preventDefault();
      event.stopPropagation();
      input.value = '';
      filter();
    }
  });
  search.hidden = false;
})();
