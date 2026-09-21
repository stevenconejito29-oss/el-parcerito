#!/usr/bin/env python3
"""Auditoría FK de solo lectura; reparación acotada y archivada de enlaces del catálogo."""
import argparse
import hashlib
import json
import os
from sqlalchemy import create_engine, inspect, text

LINKS = {
    'combo_item_allowed_flavors': [('combo_item_id','combo_items'),('option_id','product_extra_options')],
    'product_presentation_flavors': [('presentation_id','product_presentations'),('option_id','product_extra_options')],
    'combo_item_allowed_presentations': [('combo_item_id','combo_items'),('presentation_id','product_presentations')],
}


def audit(conn):
    inspector = inspect(conn)
    quote = conn.dialect.identifier_preparer.quote
    tables = inspector.get_table_names(schema='public')
    findings = []
    for table in tables:
        fks = inspector.get_foreign_keys(table, schema='public')
        for col, parent in LINKS.get(table, []):
            if not any(f['constrained_columns']==[col] and f['referred_table']==parent for f in fks):
                fks.append(dict(name=None,constrained_columns=[col],referred_columns=['id'],referred_table=parent,referred_schema='public'))
        for fk in fks:
            cols=fk['constrained_columns']; refs=fk['referred_columns']
            parent=fk['referred_table']; schema=fk.get('referred_schema') or 'public'
            present=' AND '.join(f'a.{quote(c)} IS NOT NULL' for c in cols)
            matches=' AND '.join(f'b.{quote(r)} = a.{quote(c)}' for c,r in zip(cols,refs))
            predicate=f'{present} AND NOT EXISTS (SELECT 1 FROM {quote(schema)}.{quote(parent)} b WHERE {matches})'
            count=conn.execute(text(f'SELECT count(*) FROM public.{quote(table)} a WHERE {predicate}')).scalar_one()
            if count or not fk['name']:
                findings.append(dict(table=table,columns=cols,parent=parent,orphans=count,missing_constraint=not bool(fk['name'])))
    return findings


def repair_catalog_links(conn):
    """Una transacción, FKs activas, conserva filas originales y jamás reinicia IDs."""
    quote=conn.dialect.identifier_preparer.quote
    tables=inspect(conn).get_table_names(schema='public')
    links={table:refs for table,refs in LINKS.items() if table in tables}
    # Impide que un cambio concurrente del catálogo invalide el diagnóstico.
    locked=sorted(set(links) | {p for refs in links.values() for _,p in refs})
    conn.execute(text('LOCK TABLE '+','.join('public.'+quote(t) for t in locked)+' IN SHARE ROW EXCLUSIVE MODE'))
    conn.execute(text('''CREATE TABLE IF NOT EXISTS catalog_link_quarantine (
        source_table text NOT NULL, row_hash varchar(64) NOT NULL,
        original_row jsonb NOT NULL, reason text NOT NULL,
        archived_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (source_table,row_hash))'''))
    repaired={}
    for table,refs in links.items():
        predicate=' OR '.join(f'NOT EXISTS (SELECT 1 FROM public.{quote(parent)} p WHERE p.id=a.{quote(col)})' for col,parent in refs)
        rows=conn.execute(text(f'SELECT a.* FROM public.{quote(table)} a WHERE {predicate}')).mappings().all()
        for row in rows:
            original=json.dumps(dict(row),sort_keys=True)
            conn.execute(text('''INSERT INTO catalog_link_quarantine(source_table,row_hash,original_row,reason)
                VALUES (:table,:digest,CAST(:original AS jsonb),'missing_catalog_parent')
                ON CONFLICT (source_table,row_hash) DO NOTHING'''),dict(table=table,digest=hashlib.sha256(original.encode()).hexdigest(),original=original))
        deleted=conn.execute(text(f'DELETE FROM public.{quote(table)} a WHERE {predicate}')).rowcount
        if deleted != len(rows):
            raise RuntimeError('El catálogo cambió durante la reparación; se revierte toda la transacción')
        repaired[table]=deleted
        existing=inspect(conn).get_foreign_keys(table,schema='public')
        for col,parent in refs:
            if not any(f['constrained_columns']==[col] and f['referred_table']==parent for f in existing):
                name=f'{table}_{col}_fkey'
                conn.execute(text(f'ALTER TABLE public.{quote(table)} ADD CONSTRAINT {quote(name)} FOREIGN KEY ({quote(col)}) REFERENCES public.{quote(parent)} (id) ON DELETE CASCADE'))
    return repaired


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repair-catalog-links',action='store_true',help='Archiva y retira exclusivamente enlaces a padres inexistentes; exige backup previo')
    args=parser.parse_args()
    engine=create_engine(os.environ['DATABASE_URL'])
    if engine.dialect.name!='postgresql':
        raise SystemExit('Esta auditoría operativa requiere PostgreSQL')
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL lock_timeout='5s'"))
        if not args.repair_catalog_links:
            conn.execute(text('SET TRANSACTION READ ONLY'))
        before=audit(conn)
        repaired=repair_catalog_links(conn) if args.repair_catalog_links else {}
        after=audit(conn) if args.repair_catalog_links else before
        print(json.dumps(dict(before=before,repaired=repaired,remaining=after),ensure_ascii=False,indent=2))
    return 1 if after else 0

if __name__=='__main__':
    raise SystemExit(main())
