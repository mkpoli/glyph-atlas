"""Deterministic visual-family samples, preserving unverified upstream labels."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections import Counter, defaultdict, deque
from pathlib import Path

import httpx
import pyarrow.parquet as pq
from PIL import Image, ImageStat

from . import refs, tables
from .production import production_info
from .visual_families import evidence_signature

PRIORITY = "仮国学体気竜旧変広円"
HI_ARCHIVE = "https://data.lab.hi.u-tokyo.ac.jp/kuzushiji/2023-03-27/all.zip"


def families(request=PRIORITY):
    if request == "all":
        keys = [key for key, members in refs.graphemes().items() if len(members)>1
                and (refs.grapheme_info(key) or {}).get("relation")=="shinjitai-kyujitai"]
    else:
        keys = [refs.grapheme(f"U+{ord(char):04X}") or f"U+{ord(char):04X}" for char in request]
    return {key:refs.graphemes().get(key,[key]) for key in dict.fromkeys(keys)}


def balanced(rows, limit, *, bucket="document_id"):
    groups = defaultdict(list)
    for row in rows:
        groups[row.get(bucket) or "unknown"].append(row)
    queues = {key:deque(sorted(values,key=lambda r:hashlib.sha256(r["id"].encode()).hexdigest()))
              for key,values in groups.items()}
    result=[]
    while queues and len(result)<limit:
        for key in sorted(queues):
            if len(result)>=limit:
                break
            result.append(queues[key].popleft())
            if not queues[key]:
                del queues[key]
    return result


class BudgetClient:
    """Read ZIP ranges only; reject full-file responses before reading their body."""
    def __init__(self, maximum=250*1024*1024, pause=.2):
        self.client=httpx.Client(follow_redirects=True,timeout=httpx.Timeout(30,read=60))
        self.maximum=maximum
        self.used=0
        self.pause=pause
        self.requests=0

    def head(self,url):
        self.requests+=1
        return self.client.head(url)

    def get(self,url,*,headers):
        if self.used>=self.maximum:
            raise RuntimeError("visual-sample network budget exhausted")
        requested=headers.get("Range", "").removeprefix("bytes=").split("-")
        if len(requested)!=2 or not all(part.isdigit() for part in requested):
            raise RuntimeError("visual-sample requests require a bounded ZIP range")
        if int(requested[1])-int(requested[0])+1>self.maximum-self.used:
            raise RuntimeError("ZIP range exceeds remaining network budget")
        time.sleep(self.pause)
        self.requests+=1
        with self.client.stream("GET",url,headers=headers) as response:
            if response.status_code!=206 or "Content-Range" not in response.headers:
                raise RuntimeError("server did not honor a ZIP range; body not downloaded")
            bounds = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers["Content-Range"])
            if bounds is None or [int(bounds[1]), int(bounds[2])] != list(map(int, requested)):
                raise RuntimeError("server returned a different ZIP range; body not downloaded")
            expected = int(requested[1])-int(requested[0])+1
            length=response.headers.get("Content-Length")
            if length is None or not length.isdigit() or int(length) != expected:
                raise RuntimeError("ZIP range lacks an exact Content-Length; body not downloaded")
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise RuntimeError("encoded ZIP range refused; body not downloaded")
            parts=[]
            for chunk in response.iter_bytes(chunk_size=64*1024):
                self.used+=len(chunk)
                if self.used>self.maximum:
                    raise RuntimeError("visual-sample network budget exhausted")
                parts.append(chunk)
            return httpx.Response(response.status_code,headers=response.headers,
                                  content=b"".join(parts),request=response.request)

    def close(self):
        self.client.close()


def candidates(family_map):
    point_family={member:key for key,members in family_map.items() for member in members}
    points=list(point_family)
    docs={d.id:d for name in ("codh-full","hilab")
          for d in tables.Dataset(Path("work")/name).read("documents")}
    rows=[]
    for split in ("train","val","test"):
        path=Path("work/classifier")/f"{split}.parquet"
        if not path.exists():
            continue
        table=pq.read_table(path,filters=[("code_point","in",points)])
        for row in table.to_pylist():
            crop=Path(row["crop"])
            if not crop.is_file():
                continue
            rows.append({"id":row["unit_id"],"corpus":"codh-full",
                         "document_id":row["document_id"],"page_id":row["page_id"],
                         "source_code_point":row["code_point"],"original_crop":str(crop),
                         "family":point_family[row["code_point"]],"model_training_split":split,
                         "sampling_stratum":row["document_id"]})
    columns=["id","document_id","page_id","crop","box","unicode","text_source","upstream"]
    table=pq.read_table("work/hilab/units.parquet",filters=[("unicode","in",points)],columns=columns)
    for row in table.to_pylist():
        member=row["crop"].split("!",1)[1]
        local=Path("cache/hilab")/member
        rows.append({"id":row["id"],"corpus":"hilab","document_id":row["document_id"],
                     "page_id":row["page_id"],"source_code_point":row["unicode"],
                     "original_crop":str(local) if local.is_file() else None,
                     "archive_member":member,"family":point_family[row["unicode"]],
                     "model_training_split":"not_in_baseline_training",
                     "sampling_stratum":row["unicode"],"work_identity_available":False,
                     "box":row["box"], "source_crop":row["crop"],
                     "source_signature":evidence_signature(row["id"],row["unicode"],row["page_id"],row["box"],row["crop"]),
                     "source_url":json.loads(row["upstream"]).get("url")})
    for row in rows:
        row.update(production_info(docs[row["document_id"]]))
        row["document_title"]=docs[row["document_id"]].title
        row["source_label"]=chr(int(row["source_code_point"][2:],16))  # noqa: FURB166
        row["source_label_is_verified"]=False
        row["exact_character"]=None
        row["members"]=family_map[row["family"]]
        row["member_characters"]=[chr(int(cp[2:],16)) for cp in row["members"]]  # noqa: FURB166
        row["source_rights"]=docs[row["document_id"]].image_rights.model_dump(mode="json")
    return rows


def choose(rows, per_source=128, maximum=3000):
    groups=defaultdict(list)
    for row in rows:
        groups[(row["family"],row["corpus"])].append(row)
    chosen=[]
    for (family,corpus),values in sorted(groups.items()):
        chosen.extend(balanced(values,per_source,bucket="sampling_stratum"))
    # Under an overall cap, preserve all families/sources through another round robin.
    for row in chosen:
        row["sample_group"]=row["family"]+":"+row["corpus"]
    return balanced(chosen,maximum,bucket="sample_group")


def prepare(root, *, request=PRIORITY, per_source=128, maximum=3000, network_mib=250):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    family_map=families(request)
    selected=choose(candidates(family_map),per_source,maximum)
    codh_ids=[r["id"] for r in selected if r["corpus"]=="codh-full"]
    units=pq.read_table("work/codh-full/units.parquet",filters=[("id","in",codh_ids)],
                        columns=["id","page_id","box","crop","unicode"]).to_pylist() if codh_ids else []
    pages={p.id:p for p in tables.Dataset(Path("work/codh-full")).read("pages")}
    geometry={row["id"]:row for row in units}
    for row in selected:
        if row["id"] in geometry:
            unit=geometry[row["id"]];box=unit["box"];page=pages[unit["page_id"]]
            row["box"]=box
            row["source_crop"]=unit["crop"]
            row["source_signature"]=evidence_signature(unit["id"],unit["unicode"],unit["page_id"],box,unit["crop"])
            row["image_url"]=page.image.rstrip("/")+"/"+",".join(str(box[k]) for k in ("x","y","w","h"))+"/full/0/default.jpg"
            row["source_url"]="https://codh.rois.ac.jp/char-shape/book/"+row["document_id"].split(":",1)[1]+"/"
    client=BudgetClient(maximum=network_mib*1024*1024)
    download_error=None
    missing=[r for r in selected if r["original_crop"] is None]
    if missing:
        from .remotezip import RemoteZip
        destination=root/"download-cache"
        try:
            with RemoteZip(HI_ARCHIVE,client=client) as archive:
                names=[r["archive_member"] for r in missing if not (destination/r["archive_member"]).exists()]
                archive.extract(names,destination)
            for row in missing:
                path=destination/row["archive_member"]
                if path.is_file():
                    row["original_crop"]=str(path)
        except (OSError,RuntimeError,httpx.HTTPError) as exc:
            download_error=str(exc).replace(str(Path.home()),"~")[:500]
            for row in missing:
                path=destination/row["archive_member"]
                if path.is_file():
                    row["original_crop"]=str(path)
    client.close()
    manifest=[];excluded=[];seen={};duplicates=[]
    for source in selected:
        row=dict(source)
        path=Path(row.pop("original_crop")) if row.get("original_crop") else None
        if path is None or not path.exists():
            excluded.append({"id":row["id"],"reason":"image_unavailable"});continue
        try:
            raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
            with Image.open(path) as image:
                width,height=image.size
                if min(width,height)<8 or max(width,height)>4096 or ImageStat.Stat(image.convert("L")).stddev[0]<2:
                    excluded.append({"id":row["id"],"reason":"image_quality"});continue
            if sha in seen:
                kept = seen[sha]
                excluded.append({"id":row["id"],"reason":"duplicate_bytes","duplicate_of":kept["id"]})
                duplicates.append({**row, "excluded_id":row["id"], "kept_id":kept["id"],
                                   "source_label":row["source_label"], "crop_sha256":sha,
                                   "excluded_source_label":row["source_label"],
                                   "kept_source_label":kept["source_label"], "same_crop_bytes":True,
                                   "kept_source_signature":kept["source_signature"]})
                continue
            seen[sha]=row
            relative=Path("images")/sha[:2]/(sha+path.suffix.lower())
            destination=root/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            if not destination.exists():
                shutil.copyfile(path,destination)
            row.update(row_index=len(manifest),image_path=str(relative),crop_sha256=sha,width=width,height=height,
                       quality_flags=["elongated"] if not .25<=width/height<=3 else [])
            row.pop("sample_group",None)
            manifest.append(row)
        except (OSError,ValueError) as exc:
            excluded.append({"id":row["id"],"reason":type(exc).__name__})
    temporary=root/"samples.pending.jsonl"
    temporary.write_text("".join(json.dumps(r,ensure_ascii=False,sort_keys=True)+"\n" for r in manifest))
    temporary.replace(root/"samples.jsonl")
    (root/"duplicate-sources.json").write_text(json.dumps(duplicates,ensure_ascii=False,indent=2)+"\n")
    report={"state":"ready","samples":len(manifest),"selected":len(selected),"maximum":maximum,
            "per_family_source_limit":per_source,"network_bytes":client.used,"network_requests":client.requests,
            "network_limit_bytes":client.maximum,"download_error":download_error,"excluded":excluded,
            "families":family_map,"counts":dict(Counter(r["family"]+":"+r["corpus"] for r in manifest)),
            "production":dict(Counter(r["production"] for r in manifest)),
            "label_policy":"upstream labels select a family; no exact written identity is asserted",
            "sampling":"round robin by work for CODH; upstream label strata for HI Lab because work IDs are unavailable",
            "manifest_sha256":hashlib.sha256((root/"samples.jsonl").read_bytes()).hexdigest()}
    (root/"samples-metadata.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report
