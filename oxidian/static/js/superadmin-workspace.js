(() => {
  function revealSection() {
    const id = window.location.hash.slice(1);
    const target = id && document.getElementById(id);
    if (!(target instanceof HTMLDetailsElement)) return;
    target.open = true;
    target.scrollIntoView({block: 'start'});
  }
  window.addEventListener('hashchange', revealSection);
  document.querySelectorAll('a[href="#modulos"]').forEach(link => {
    link.addEventListener('click', () => {
      const section = document.getElementById('modulos');
      if (section) section.open = true;
    });
  });
  revealSection();
})();
