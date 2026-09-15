#!/usr/bin/env python3
"""Entrada legacy retirada: la limpieza sin claves foráneas dejaba datos huérfanos.

--dry-run ejecuta ahora la auditoría de integridad, sin borrar datos.
Las invocaciones de escritura se rechazan, incluso con el antiguo token.
"""
import sys
from audit_data_integrity import main

if __name__ == '__main__':
    if sys.argv[1:] != ['--dry-run']:
        raise SystemExit('Limpieza bloqueada: este script omitía relaciones y reiniciaba IDs. Usa audit_data_integrity.py para revisar la base sin borrar datos.')
    sys.argv=[sys.argv[0]]
    raise SystemExit(main())
