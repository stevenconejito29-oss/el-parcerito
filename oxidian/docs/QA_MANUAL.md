# Prueba local integral

Arranque desde `oxidian/`:

```bash
./scripts/start_qa.sh
```

Abre `http://127.0.0.1:5071`. La contraseña local común es
`Oxidian-QA-2026` y puede cambiarse con `QA_PASSWORD=...`.

| Rol | Cuenta |
|---|---|
| Superadmin | `qa.superadmin@elparcerito.local` |
| Administración | `qa.admin@elparcerito.local` |
| Cocina inmediata | `qa.cocina@elparcerito.local` |
| Encargos programados | `qa.preparacion@elparcerito.local` |
| Reparto | `qa.repartidor@elparcerito.local` |

## Recorrido recomendado

1. Entra como superadmin y revisa clientes, teléfonos, productos y combos.
2. Crea un combo usando únicamente productos del catálogo activo.
3. Abre la tienda en una ventana privada, añade el combo y completa un pedido.
4. Activa Cocina, toma el pedido y márcalo listo.
5. Activa Reparto, sal a entregar, envía el código y confirma el cobro.
6. Revisa Caja, pagos, comisión del repartidor e historial del cliente.
7. Cambia el nicho desde Superadmin y comprueba que el catálogo anterior no se mezcla.

Para borrar completamente el entorno QA:

```bash
./scripts/stop_qa.sh
```

Este entorno usa una base efímera independiente. No conecta con producción.
