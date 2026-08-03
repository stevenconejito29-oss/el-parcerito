(() => {
  "use strict";
  const form = document.querySelector("[data-partner-combo-form]");
  if (!form) return;

  // ── Catálogos precargados desde el server ──────────────────────────────
  const productosData = JSON.parse(
    document.getElementById("partner-combo-products")?.textContent || "[]"
  );
  const presentacionesPorProducto = JSON.parse(
    document.getElementById("partner-combo-presentations")?.textContent || "{}"
  );
  const flavorOptionsByProduct = JSON.parse(
    document.getElementById("partner-combo-flavors")?.textContent || "{}"
  );
  const initialRows = JSON.parse(
    document.getElementById("partner-combo-initial")?.textContent || "[]"
  );

  const productoById = new Map(productosData.map((p) => [Number(p.id), p]));
  const container = form.querySelector("[data-partner-rows]");
  const template = document.getElementById("partner-combo-row-template");
  const addBtn = form.querySelector("[data-partner-add-row]");
  const counter = form.querySelector("[data-selected-count]");
  const serial = form.querySelector("[data-partner-serial]");

  const uid = () => "grp-" + Math.random().toString(36).slice(2, 9);

  const state = {
    rows: [], // {id, productId, qty, mode, groupName, maxSel, presMode, presId, allowedPres[], flavorMode, fixedFlavorId, allowedFlavors[]}
  };

  function makeRow(overrides = {}) {
    return Object.assign(
      {
        id: uid(),
        productId: 0,
        qty: 1,
        mode: "fijo", // "fijo" | "eleccion"
        groupName: "",
        maxSel: 1,
        presMode: "fijo",
        presId: "",
        allowedPres: [],
        flavorMode: "sin_sabor",
        fixedFlavorId: "",
        allowedFlavors: [],
      },
      overrides
    );
  }

  function renderRow(row) {
    const node = template.content.firstElementChild.cloneNode(true);
    node.dataset.rowId = row.id;

    // Selector de producto
    const prodSel = node.querySelector('[data-field="productId"]');
    prodSel.appendChild(new Option("— Elige un producto —", "0"));
    productosData.forEach((p) => {
      const opt = new Option(p.nombre, String(p.id));
      if (row.productId === p.id) opt.selected = true;
      prodSel.appendChild(opt);
    });

    node.querySelector('[data-field="qty"]').value = row.qty;
    node.querySelector('[data-field="mode"]').value = row.mode;
    node.querySelector('[data-field="groupName"]').value = row.groupName;
    node.querySelector('[data-field="maxSel"]').value = row.maxSel;
    node.querySelector('[data-field="presMode"]').value = row.presMode;
    node.querySelector('[data-field="flavorMode"]').value = row.flavorMode;

    container.appendChild(node);
    refreshRow(row.id);
  }

  function findRow(id) {
    return state.rows.find((r) => r.id === id);
  }

  function refreshRow(id) {
    const row = findRow(id);
    if (!row) return;
    const node = container.querySelector(`[data-row-id="${id}"]`);
    if (!node) return;

    // Grupo de elección: solo visible si mode=eleccion
    node
      .querySelector('[data-block="group"]')
      .classList.toggle("hidden", row.mode !== "eleccion");

    // Bloque presentación
    const presCatalog = presentacionesPorProducto[String(row.productId)] || [];
    const presFixed = node.querySelector('[data-block="pres-fijo"]');
    const presMulti = node.querySelector('[data-block="pres-multi"]');
    const presModeSel = node.querySelector('[data-field="presMode"]');
    if (presCatalog.length <= 1) {
      // no hay tamaños alternativos: ocultamos todo el sub-bloque
      node.querySelector('[data-block="pres"]').classList.add("hidden");
      row.presMode = "fijo";
      row.presId = presCatalog[0]?.id || "";
      row.allowedPres = [];
      presModeSel.value = "fijo";
    } else {
      node.querySelector('[data-block="pres"]').classList.remove("hidden");
      if (row.presMode === "cliente_elige") {
        presFixed.classList.add("hidden");
        presMulti.classList.remove("hidden");
        // Re-render checkboxes
        presMulti.innerHTML = "";
        presCatalog.forEach((p) => {
          const id = `pres-${row.id}-${p.id}`;
          const wrap = document.createElement("label");
          wrap.className = "partner-check-inline";
          wrap.innerHTML = `<input type="checkbox" id="${id}" value="${p.id}" ${
            row.allowedPres.includes(Number(p.id)) ? "checked" : ""
          }><span>${p.label || p["tamaño"] || "Tamaño"}</span>`;
          wrap.querySelector("input").addEventListener("change", (e) => {
            const v = Number(e.target.value);
            if (e.target.checked) {
              if (!row.allowedPres.includes(v)) row.allowedPres.push(v);
            } else {
              row.allowedPres = row.allowedPres.filter((x) => x !== v);
            }
          });
          presMulti.appendChild(wrap);
        });
      } else {
        presMulti.classList.add("hidden");
        presFixed.classList.remove("hidden");
        const sel = presFixed.querySelector("select");
        sel.innerHTML = "";
        sel.appendChild(new Option("Predeterminado", ""));
        presCatalog.forEach((p) => {
          const opt = new Option(p.label || p["tamaño"] || "Tamaño", String(p.id));
          if (String(row.presId) === String(p.id)) opt.selected = true;
          sel.appendChild(opt);
        });
      }
    }

    // Bloque sabor
    const flavorCatalog = flavorOptionsByProduct[String(row.productId)] || [];
    const flavorBlock = node.querySelector('[data-block="flavor"]');
    const flavorFijo = node.querySelector('[data-block="flavor-fijo"]');
    const flavorMulti = node.querySelector('[data-block="flavor-multi"]');
    const flavorModeSel = node.querySelector('[data-field="flavorMode"]');
    if (flavorCatalog.length === 0) {
      flavorBlock.classList.add("hidden");
      row.flavorMode = "sin_sabor";
      row.fixedFlavorId = "";
      row.allowedFlavors = [];
      flavorModeSel.value = "sin_sabor";
    } else {
      flavorBlock.classList.remove("hidden");
      if (row.flavorMode === "fijo") {
        flavorFijo.classList.remove("hidden");
        flavorMulti.classList.add("hidden");
        const sel = flavorFijo.querySelector("select");
        sel.innerHTML = "";
        sel.appendChild(new Option("— Elige un sabor —", ""));
        flavorCatalog.forEach((f) => {
          const opt = new Option(f.nombre, String(f.id));
          if (String(row.fixedFlavorId) === String(f.id)) opt.selected = true;
          sel.appendChild(opt);
        });
      } else if (row.flavorMode === "cliente_elige") {
        flavorFijo.classList.add("hidden");
        flavorMulti.classList.remove("hidden");
        flavorMulti.innerHTML = "";
        flavorCatalog.forEach((f) => {
          const id = `flavor-${row.id}-${f.id}`;
          const wrap = document.createElement("label");
          wrap.className = "partner-check-inline";
          wrap.innerHTML = `<input type="checkbox" id="${id}" value="${f.id}" ${
            row.allowedFlavors.includes(Number(f.id)) ? "checked" : ""
          }><span>${f.nombre}</span>`;
          wrap.querySelector("input").addEventListener("change", (e) => {
            const v = Number(e.target.value);
            if (e.target.checked) {
              if (!row.allowedFlavors.includes(v)) row.allowedFlavors.push(v);
            } else {
              row.allowedFlavors = row.allowedFlavors.filter((x) => x !== v);
            }
          });
          flavorMulti.appendChild(wrap);
        });
      } else {
        flavorFijo.classList.add("hidden");
        flavorMulti.classList.add("hidden");
      }
    }
  }

  function updateCounter() {
    if (counter) counter.textContent = String(state.rows.length);
  }

  function addRow(overrides) {
    const row = makeRow(overrides);
    state.rows.push(row);
    renderRow(row);
    updateCounter();
  }

  function removeRow(id) {
    const node = container.querySelector(`[data-row-id="${id}"]`);
    node?.remove();
    state.rows = state.rows.filter((r) => r.id !== id);
    updateCounter();
  }

  container.addEventListener("change", (e) => {
    const rowNode = e.target.closest("[data-row-id]");
    if (!rowNode) return;
    const row = findRow(rowNode.dataset.rowId);
    if (!row) return;
    const field = e.target.dataset.field;
    if (!field) return;
    if (field === "productId") {
      row.productId = Number(e.target.value) || 0;
      // Al cambiar producto, resetear sabor/presentación por seguridad
      row.presMode = "fijo";
      row.presId = "";
      row.allowedPres = [];
      row.flavorMode = "sin_sabor";
      row.fixedFlavorId = "";
      row.allowedFlavors = [];
      refreshRow(row.id);
    } else if (field === "qty") {
      row.qty = Math.max(1, parseInt(e.target.value, 10) || 1);
    } else if (field === "mode") {
      row.mode = e.target.value;
      refreshRow(row.id);
    } else if (field === "groupName") {
      row.groupName = e.target.value;
    } else if (field === "maxSel") {
      row.maxSel = Math.max(1, parseInt(e.target.value, 10) || 1);
    } else if (field === "presMode") {
      row.presMode = e.target.value;
      row.allowedPres = [];
      refreshRow(row.id);
    } else if (field === "presFixed") {
      row.presId = e.target.value;
    } else if (field === "flavorMode") {
      row.flavorMode = e.target.value;
      row.fixedFlavorId = "";
      row.allowedFlavors = [];
      refreshRow(row.id);
    } else if (field === "fixedFlavor") {
      row.fixedFlavorId = e.target.value;
    }
  });

  container.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-partner-remove-row]");
    if (btn) {
      const rowNode = btn.closest("[data-row-id]");
      if (rowNode) removeRow(rowNode.dataset.rowId);
    }
  });

  addBtn?.addEventListener("click", () => addRow());

  // ── Serialización al submit (arrays paralelos comp_*) ─────────────────
  form.addEventListener("submit", (e) => {
    serial.innerHTML = "";
    const add = (name, value) => {
      const inp = document.createElement("input");
      inp.type = "hidden";
      inp.name = name;
      inp.value = value == null ? "" : String(value);
      serial.appendChild(inp);
    };

    // Grupos: cada eleccion con groupName diferente crea uno; los fijos van todos a __fixed__.
    const groupUids = {}; // groupKey -> uid
    let groupOrder = 0;
    state.rows.forEach((row) => {
      let key;
      if (row.mode === "eleccion") {
        key = "sel::" + (row.groupName || "").trim().toLowerCase();
      } else {
        key = "__fixed__";
      }
      if (!(key in groupUids)) {
        const gUid = row.mode === "eleccion" ? `sel-${uid()}` : `fixed-${uid()}`;
        groupUids[key] = gUid;
        add("combo_group_uid", gUid);
        add(
          "combo_group_name",
          row.mode === "eleccion" ? (row.groupName || "").trim() : ""
        );
        add("combo_group_type", row.mode === "eleccion" ? "sel" : "fijo");
        add("combo_group_max_sel", row.mode === "eleccion" ? row.maxSel : 1);
        add("combo_group_order", groupOrder++);
      }
    });

    state.rows.forEach((row) => {
      const key =
        row.mode === "eleccion"
          ? "sel::" + (row.groupName || "").trim().toLowerCase()
          : "__fixed__";
      const gUid = groupUids[key];
      add("comp_group_uid", gUid);
      add("comp_prod_id", row.productId);
      add("comp_cantidad", row.qty);
      add("comp_tipo", row.mode === "eleccion" ? "sel" : "fijo");
      add("comp_grupo", row.mode === "eleccion" ? (row.groupName || "").trim() : "");
      add("comp_max_sel", row.mode === "eleccion" ? row.maxSel : 1);
      add("comp_precio_extra", 0);
      add("comp_default", 0);
      add("comp_notas_preparacion", "");
      add("comp_presentation_id", row.presMode === "fijo" ? row.presId || "" : "");
      add("comp_permite_sabor", row.flavorMode === "cliente_elige" ? "1" : "0");
      add("comp_flavor_mode", row.flavorMode);
      add(
        "comp_fixed_flavor_id",
        row.flavorMode === "fijo" ? row.fixedFlavorId || "" : ""
      );
      add(
        "comp_allowed_flavor_ids",
        row.flavorMode === "cliente_elige" && row.allowedFlavors.length
          ? JSON.stringify(row.allowedFlavors)
          : ""
      );
      add("comp_presentation_mode", row.presMode);
      add(
        "comp_allowed_presentation_ids",
        row.presMode === "cliente_elige" && row.allowedPres.length >= 2
          ? JSON.stringify(row.allowedPres)
          : ""
      );
    });
  });

  // ── Inicialización ─────────────────────────────────────────────────────
  if (initialRows.length) {
    initialRows.forEach((r) => addRow(r));
  }
  updateCounter();
})();
