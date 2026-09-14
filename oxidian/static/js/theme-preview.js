(() => {
  'use strict';
  const preview = document.querySelector('[data-theme-preview]');
  if (!preview) return;
  const inputs = [...document.querySelectorAll('input[type="color"]')];
  const read = name => inputs.find(input => input.name === name)?.value;
  const luminance = hex => {
    const rgb = [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255)
      .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
  };
  const ratio = (a, b) => {
    const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (values[0] + 0.05) / (values[1] + 0.05);
  };
  function update() {
    const foreground = read('COLOR_TEXTO');
    const surface = read('COLOR_SUPERFICIE');
    const header = read('COLOR_CABECERA_FONDO');
    const headerText = read('COLOR_CABECERA_TEXTO');
    preview.style.backgroundColor = read('COLOR_FONDO_APP');
    preview.style.color = foreground;
    const card = preview.querySelector('[data-theme-preview-card]');
    card.style.backgroundColor = surface;
    const top = preview.querySelector('[data-theme-preview-header]');
    top.style.backgroundColor = header;
    top.style.color = headerText;
    preview.querySelector('[data-theme-preview-text]').style.color = read('COLOR_TEXTO_SUAVE');
    const button = preview.querySelector('[data-theme-preview-button]');
    const accent = read('COLOR_ACENTO');
    const onColor = color => ratio(color, '#18120a') >= ratio(color, '#ffffff') ? '#18120a' : '#ffffff';
    button.style.backgroundColor = accent;
    button.style.color = onColor(accent);
    preview.querySelector('[data-theme-preview-price]').style.color = read('COLOR_PRIMARIO');
    const badge = preview.querySelector('[data-theme-preview-secondary]');
    badge.style.backgroundColor = read('COLOR_SECUNDARIO');
    badge.style.color = onColor(read('COLOR_SECUNDARIO'));
    const pairs = [[foreground, surface, 'texto principal'], [read('COLOR_TEXTO_SUAVE'), surface, 'texto secundario'],
      [headerText, header, 'cabecera']];
    const low = pairs.filter(([a, b]) => a && b && ratio(a, b) < 4.5).map(pair => pair[2]);
    preview.querySelector('[data-theme-contrast]').textContent = low.length
      ? `Revisa el contraste de ${low.join(', ')}: algunos textos pueden ser difíciles de leer.`
      : 'Las combinaciones de texto de esta muestra tienen buen contraste.';
    const dirty = inputs.some(input => input.value.toLowerCase() !== input.defaultValue.toLowerCase());
    preview.querySelector('[data-theme-preview-note]').textContent = dirty
      ? 'Vista previa con cambios sin guardar. Guarda cada sección que hayas modificado.'
      : 'Los cambios se aplican a la tienda cuando guardas cada sección.';
  }
  inputs.forEach(input => input.addEventListener('input', update));
  new Set(inputs.map(input => input.form).filter(Boolean)).forEach(form =>
    form.addEventListener('reset', () => setTimeout(update, 0)));
  update();
})();
