(() => {
  const dayNames = [
    "Lunes", "Martes", "Miércoles", "Jueves",
    "Viernes", "Sábado", "Domingo",
  ];

  document.querySelectorAll("[data-partner-schedule]").forEach((root) => {
    const input = document.getElementById(root.dataset.inputId);
    const host = root.querySelector("[data-schedule-days]");
    if (!input || !host) return;

    let schedule = {};
    try {
      schedule = JSON.parse(input.value || "{}");
    } catch (_) {
      schedule = {};
    }
    const configured = Object.keys(schedule).some(
      (day) => Array.isArray(schedule[day])
    );
    if (!configured && root.dataset.legacyOpen && root.dataset.legacyClose) {
      schedule = Object.fromEntries(dayNames.map((_, day) => [
        String(day),
        [[root.dataset.legacyOpen, root.dataset.legacyClose]],
      ]));
    }

    const serialize = () => {
      const result = {};
      root.querySelectorAll("[data-schedule-day]").forEach((card) => {
        result[card.dataset.scheduleDay] = [
          ...card.querySelectorAll("[data-schedule-window]"),
        ].map((row) => [
          row.querySelector("[data-window-start]").value,
          row.querySelector("[data-window-end]").value,
        ]).filter(([start, end]) => start && end);
      });
      input.value = JSON.stringify(result);
    };

    const syncClosed = (card) => {
      card.querySelector("[data-day-closed]").hidden = Boolean(
        card.querySelector("[data-schedule-window]")
      );
    };

    const addWindow = (card, values = ["", ""]) => {
      const row = document.createElement("div");
      row.className = "partner-schedule__window";
      row.dataset.scheduleWindow = "";
      row.innerHTML = `
        <label><span>Abre</span><input type="time" data-window-start required></label>
        <label><span>Cierra</span><input type="time" data-window-end required></label>
        <button type="button" data-window-remove aria-label="Eliminar franja">×</button>`;
      row.querySelector("[data-window-start]").value = values[0] || "";
      row.querySelector("[data-window-end]").value = values[1] || "";
      row.querySelector("[data-window-remove]").addEventListener("click", () => {
        row.remove();
        syncClosed(card);
        serialize();
      });
      row.querySelectorAll("input").forEach((field) => {
        field.addEventListener("change", serialize);
      });
      card.querySelector("[data-day-windows]").appendChild(row);
      syncClosed(card);
    };

    dayNames.forEach((name, day) => {
      const card = document.createElement("section");
      card.className = "partner-schedule__day";
      card.dataset.scheduleDay = String(day);
      card.innerHTML = `
        <header>
          <div><strong>${name}</strong><small data-day-closed>Cerrado</small></div>
          <button type="button" data-window-add>+ Franja</button>
        </header>
        <div class="partner-schedule__windows" data-day-windows></div>`;
      host.appendChild(card);
      card.querySelector("[data-window-add]").addEventListener("click", () => {
        addWindow(card);
        card.querySelector("[data-schedule-window]:last-child input")?.focus();
        serialize();
      });
      (schedule[String(day)] || []).forEach((values) => addWindow(card, values));
      syncClosed(card);
    });

    root.querySelector("[data-schedule-copy]")?.addEventListener("click", () => {
      const monday = [...root.querySelectorAll(
        '[data-schedule-day="0"] [data-schedule-window]'
      )].map((row) => [
        row.querySelector("[data-window-start]").value,
        row.querySelector("[data-window-end]").value,
      ]);
      root.querySelectorAll("[data-schedule-day]").forEach((card, day) => {
        if (day === 0) return;
        card.querySelector("[data-day-windows]").replaceChildren();
        monday.forEach((values) => addWindow(card, values));
        syncClosed(card);
      });
      serialize();
    });
    root.closest("form")?.addEventListener("submit", serialize);
    serialize();
  });
})();
