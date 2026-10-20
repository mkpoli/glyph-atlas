"""Copy the published tables out of the live D1 into source.sqlite, a bounded keyset read at a time.

Each read is a primary-key range of at most CHUNK rows, so no read scans and the site keeps answering.
A rerun continues from the last key copied.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Copies of the live tables and the build output live outside the repository.
HERE = Path(os.environ.get("ATLAS3_DIR", ROOT / "work/atlas-v3"))
CF = ROOT / "apps/cloudflare"
db = None

# table: (columns, key columns, chunk)
TABLES = {
    "units": ("id,origin,character,family,visual_group,production,category,state,revision,quiz,shuffle,document,style,written_form,data", ["rowid"], 1500),
    "corpus_units": ("id,character,family,visual_group,shuffle,object,offset,size,production,named,style,document,label", ["rowid"], 8000),
    "media": ("key,object,offset,size,content_type", ["rowid"], 15000),
    "unit_ngrams": ("first,size,second,third,text,document,vertical,hand_order,shuffle,graphemes", ["first", "size"], 8000),
    "pages": ("id,document,data", ["id"], 2000),
    "document_dating": ("document,axis,kind,start,end,precision,qualifier,uncertain,text,label,status,calendar,conversion,source",
                        ["document", "axis"], 4000),
    "unit_redirects": ("id,target", ["id"], 8000),
    # Book facts and the site-wide values books.py moves.
    "corpus_documents": ("document,title", ["document"], 8000),
    "document_characters": ("document,ord,unit,data", ["document", "ord"], 2000),
    "metadata": ("key,value", ["key"], 100),
    # Review history, claims and the crop marks the cutover moves with integer crop ids.
    "submissions": ("id,actor,request,response,at,undone", ["id"], 2000),
    "events": ("id,submission,target,actor,expected_revision,before_data,after_data,event,snapshot,kind,at,processed", ["id"], 300),
    "skips": ("target,submission,actor,box,image_sha256,at", ["target", "submission"], 5000),
    "seen": ("target,submission,box,image_sha256,at", ["target", "submission"], 5000),
    "actors": ("actor,user_id,via,at", ["actor"], 8000),
    "favourites": ("user_id,unit,at", ["user_id", "unit"], 8000),
    "written_forms": ("id,submission,target,actor,revision,pixels,label,form,request,at", ["id"], 1000),
    "assertions": (("id,submission,subject,predicate,scope,slot,object,value,alternative_set,tier,asserted_by,asserted_at,"
                    "confidence,confidence_scheme,method,run,legacy"), ["id"], 2000),
    "assertion_actions": ("id,submission,assertion,action,actor,at,reason", ["id"], 4000),
    "assertion_evidence": ("assertion,kind,ref,locator", ["assertion", "kind", "ref"], 8000),
    "assertion_premises": ("assertion,premise,role", ["assertion", "premise"], 8000),
    "current_claims": ("subject,predicate,scope,slot,status,object,value,members,supporting,claims,crop_version,resolver,at",
                       ["subject", "predicate", "scope", "slot"], 2000),
    "ledger_submissions": ("id,actor,request,response,at", ["id"], 1000),
    "rejections": ('submission,"by",reason,at', ["submission"], 8000),
    "form_decisions": ("seq,id,at,actor,kind,family,form,cluster,revision,units,note,issue,character,written_family", ["seq"], 1000),
    "form_marks": ("id,at,seq,kind,form,decision,issue,character,written_family", ["rowid"], 5000),
    "crop_versions": ("id,unit,pixels,box,document,image,at", ["id"], 5000),
    "unit_marks": ("id,mark", ["id"], 8000),
    "unit_suspects": ("id,p,reads_as,label,box", ["id"], 8000),
    "unit_shapes": ("id,shape_order", ["id"], 8000),
    "forms": ("id,anchor,created_by,created_at", ["id"], 8000),
    "corpus_follow": ("id", ["id"], 8000),
    "aliases": ("query,code_point,rank", ["rowid"], 15000),
    # Character reference data (reference.py). A card averages 5 KB, at most 85 KB.
    "characters": ("code_point,character,name,data,detail,json_extract(data,'$.grapheme.code_point') AS head", ["code_point"], 300),
    "character_variants": ("a,b,relation,source,detail,written,widens", ["a", "b", "relation", "source"], 8000),
    "character_derived": ("a,forms", ["a"], 500),
    "component_variants": ("a,b,count,pairs", ["a", "b"], 1000),
    "grapheme_writers": ("writer,grapheme,own", ["writer", "grapheme"], 8000),
    "words": ("id,language,reading,class", ["id"], 8000),
    "word_spellings": ("word,spelling,source,locator,tier,word_by,basis,related,statement,documents,occurrences",
                       ["word", "spelling", "source", "locator"], 2000),
    "han_ids": ("char,sequences,shapes", ["char"], 4000),
    "han_components": ("component,tier,size,code_point,n,direct", ["component", "tier", "size", "code_point"], 8000),
    "han_component_names": ("query,component,total", ["query"], 8000),
}


def query(sql):
    # This copies out of the live database; it never writes there.
    assert sql.lstrip().upper().startswith("SELECT"), sql
    for attempt in range(6):
        out = subprocess.run([shutil.which("bunx") or "bunx", "wrangler", "d1", "execute", "glyph-atlas", "--remote", "--json", "--command", sql],
                             cwd=CF, capture_output=True, text=True, check=False)
        try:
            body = json.loads(out.stdout)
            if isinstance(body, list):
                return body[0]["results"]
            print("error", json.dumps(body)[:300], flush=True)
        except json.JSONDecodeError:
            print("bad output", out.stdout[:200], out.stderr[-300:], flush=True)
        time.sleep(10 * (attempt + 1))
    sys.exit(f"gave up on: {sql[:200]}")


def pull(table, fresh):
    global db
    path = HERE / f"source-{table}.sqlite"
    if fresh:
        path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    cols, keys, chunk = TABLES[table]
    names = [c.split(" AS ")[-1].strip().strip('"') for c in cols.split(",")]
    if table == "characters":
        names = ["code_point", "character", "name", "data", "detail", "head"]
    keycols = [k for k in keys if k != "rowid"]
    store = (["_rowid"] if keys == ["rowid"] else []) + names
    db.execute(f'CREATE TABLE IF NOT EXISTS "{table}"({",".join(store)})')
    db.execute('CREATE TABLE IF NOT EXISTS _progress(tbl PRIMARY KEY, key TEXT)')
    row = db.execute("SELECT key FROM _progress WHERE tbl=?", (table,)).fetchone()
    if row and json.loads(row[0]) == "done":
        # Resuming only copies rows past the last key, so a finished copy never sees later edits.
        sys.exit(f"{table} is already copied; --fresh copies it again from the start")
    last = json.loads(row[0]) if row else None
    select = ("rowid AS _rowid," if keys == ["rowid"] else "") + cols
    order = ",".join(keys)
    n = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    while True:
        if last is None:
            where = ""
        elif len(keys) == 1:
            where = f"WHERE {keys[0]} > {json.dumps(last[0]) if isinstance(last[0], (int, float)) else sql_str(last[0])}"
        else:
            where = f"WHERE ({order}) > ({','.join(sql_lit(v) for v in last)})"
        rows = query(f'SELECT {select} FROM "{table}" {where} ORDER BY {order} LIMIT {chunk}')
        if not rows:
            break
        db.executemany(f'INSERT INTO "{table}" VALUES({",".join("?" * len(store))})', [[r[c] for c in store] for r in rows])
        tail = rows[-1]
        last = [tail["_rowid"]] if keys == ["rowid"] else [tail[k] for k in keycols]
        db.execute("INSERT OR REPLACE INTO _progress VALUES(?,?)", (table, json.dumps(last)))
        db.commit()
        n += len(rows)
        print(f"{table} {n}", flush=True)
        if len(rows) < chunk:
            break
    db.execute("INSERT OR REPLACE INTO _progress VALUES(?,?)", (table, json.dumps("done")))
    db.commit()
    print(f"{table} done {n}", flush=True)


def sql_str(s):
    return "'" + str(s).replace("'", "''") + "'"


def sql_lit(v):
    return str(v) if isinstance(v, (int, float)) else sql_str(v)


if __name__ == "__main__":
    fresh = "--fresh" in sys.argv
    for t in [a for a in sys.argv[1:] if a != "--fresh"] or TABLES:
        pull(t, fresh)
