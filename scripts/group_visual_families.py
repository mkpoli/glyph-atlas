"""Cluster cached family crops on CUDA; publish versioned groups and prototypes.

Upstream character labels are retained for provenance and never used as training
targets. Optional explicit visual anchors name a group only when they agree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

import numpy as np
import torch
import torch.nn.functional as F


def kmeans(x, k, seed):
    generator = torch.Generator(device=x.device).manual_seed(seed)
    first = int(torch.randint(len(x), (1,), generator=generator, device=x.device))
    centers = [x[first]]
    for _ in range(1, k):
        distance = (1 - x @ torch.stack(centers).T).min(dim=1).values.clamp_min(0)
        centers.append(x[int(distance.argmax())])
    c = torch.stack(centers)
    old = None
    for _ in range(60):
        labels = (x @ c.T).argmax(dim=1)
        if old is not None and torch.equal(labels, old):
            break
        old = labels.clone()
        updated = []
        for i in range(k):
            subset = x[labels == i]
            updated.append(F.normalize(subset.mean(0), dim=0) if len(subset) else c[i])
        c = torch.stack(updated)
    return labels, c


def silhouette(distance, labels):
    labels_present = labels.unique()
    if len(labels_present) == 1:
        return 0.0
    scores = torch.zeros(len(labels), device=distance.device)
    for group in labels_present:
        mask = labels == group
        n = int(mask.sum())
        if n < 2:
            scores[mask] = -1
            continue
        a = distance[mask][:, mask].sum(1) / (n - 1)
        b = torch.stack([distance[mask][:, labels == other].mean(1)
                         for other in labels_present if other != group]).min(0).values
        scores[mask] = (b - a) / torch.maximum(a, b).clamp_min(1e-8)
    return float(scores.mean())


def choose_groups(x):
    distance = (1 - x @ x.T).clamp_min(0)
    best = (0.0, torch.zeros(len(x), dtype=torch.long, device=x.device),
            F.normalize(x.mean(0), dim=0)[None], 0.0)
    for k in range(2, min(8, len(x) // 5) + 1):
        for seed in (11, 29, 53):
            labels, centers = kmeans(x, k, seed)
            score = silhouette(distance, labels)
            objective = score - .025 * (k - 1)
            if min(int((labels == i).sum()) for i in range(k)) < 3:
                continue
            if objective > best[0] + .01:
                best = objective, labels, centers, score
    return best[1], best[2], best[3]


def atomic_json(path, data):
    temp = path.with_suffix(path.suffix + ".next")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(temp, path)


def anchored_support(members, distances, anchors, radius):
    """Name only a supported central region, bounded by inspected counterexamples."""
    votes = [{**anchors[m["id"]], "id": m["id"], "distance": float(d)}
             for m, d in zip(members, distances, strict=True) if m["id"] in anchors]
    counts = Counter(v.get("character") for v in votes if v.get("character"))
    for name, count in counts.most_common():
        if count < 6:
            continue
        counterexamples = [v["distance"] for v in votes if v.get("character") != name]
        support = min(radius, min(counterexamples) - .005) if counterexamples else radius
        if sum(v.get("character") == name and v["distance"] <= support for v in votes) >= 6:
            return name, max(0, support), votes
    return None, radius, votes


def run(root, anchors_path=None):
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA required for the bounded visual-family grouping pass")
    torch.set_num_threads(2)
    torch.cuda.set_per_process_memory_fraction(.18)
    manifest_sha = hashlib.sha256((root / "samples.jsonl").read_bytes()).hexdigest()
    embedding_sha = hashlib.sha256((root / "embeddings.npy").read_bytes()).hexdigest()
    metadata = json.loads((root / "embeddings-metadata.json").read_text())
    if (metadata.get("manifest_sha256") != manifest_sha
            or metadata.get("embeddings_sha256") != embedding_sha):
        raise ValueError("Embedding provenance does not match the current sample manifest and vectors")
    encoder = Path("models/classifier/artifacts/classifier-with-features.onnx")
    encoder_sha = hashlib.sha256(encoder.read_bytes()).hexdigest()
    parity = json.loads((root / "onnx-parity.json").read_text())
    if (not parity.get("passed") or parity.get("manifest_sha256") != manifest_sha
            or parity.get("checkpoint_sha256") != metadata.get("checkpoint_sha256")
            or parity.get("onnx_sha256", {}).get(encoder.name) != encoder_sha):
        raise ValueError("Encoder has no matching embedding parity validation")
    rows = [json.loads(line) for line in (root / "samples.jsonl").read_text().splitlines() if line]
    vectors = np.load(root / "embeddings.npy", allow_pickle=False)
    if len(vectors) != len(rows) or vectors.ndim != 2 or not np.isfinite(vectors).all():
        raise ValueError("Manifest and embeddings disagree")
    anchors = json.loads(anchors_path.read_text()) if anchors_path else {}
    indexed = {row["id"]: row for row in rows}
    for identity, anchor in anchors.items():
        row = indexed.get(identity)
        if row is None or any(not anchor.get(key) or anchor[key] != row.get(key)
                              for key in ("crop_sha256", "source_signature")):
            raise ValueError("Visual inspection has stale or missing crop provenance")
    revision = hashlib.sha256((manifest_sha + embedding_sha + json.dumps(anchors, sort_keys=True)
                              + "spherical-family-v2").encode()).hexdigest()[:20]
    families = defaultdict(list)
    for index, row in enumerate(rows):
        if row.get("row_index", index) != index:
            raise ValueError("Embedding row index mismatch")
        families[row["family"]].append(index)
    output = {"version": 1, "model_revision": revision, "manifest_sha256": manifest_sha,
              "embedding_sha256": embedding_sha, "families": {}, "assignments": {}}
    head_groups, head_centroids, head_radii = [], [], []
    for family, indexes in sorted(families.items()):
        x = F.normalize(torch.as_tensor(vectors[indexes], device="cuda"), dim=1)
        labels, centers, score = choose_groups(x)
        groups = []
        # Stable numbering by medoid identity, independent of the KMeans center order.
        order = sorted(range(len(centers)), key=lambda c: rows[indexes[int(
            (x @ centers[c]).masked_fill(labels != c, -2).argmax())]]["id"])
        for serial, c in enumerate(order, 1):
            local = torch.where(labels == c)[0].tolist()
            members = [rows[indexes[i]] for i in local]
            distances = (1 - x[local] @ centers[c]).clamp_min(0)
            radius = min(.35, float(torch.quantile(distances, .95)) + .025)
            representative_order = torch.argsort(distances).tolist()
            # Require agreeing visual anchors; normalized source bins supply no votes.
            written, radius, votes = anchored_support(members, distances.cpu().tolist(), anchors, radius)
            assignment_enabled = not any(v.get("blocks_group_assignment") for v in votes)
            member_characters = members[0].get("member_characters", []) or [
                chr(int(v[2:], 16)) if isinstance(v, str) and v.startswith("U+") else v  # noqa: FURB166
                for v in members[0]["members"]]
            if written and written not in member_characters:
                raise ValueError("An anchor assigns a character outside the family")
            group_id = f"{family}:{hashlib.sha256(members[0]['id'].encode()).hexdigest()[:10]}"
            representatives = [{"id": members[i]["id"],
                                "image": "/layers/visual-groups/samples/" + quote(members[i]["id"], safe="") + "/image",
                                "source_label": members[i]["source_label"],
                                "production": members[i].get("production", "unknown"),
                                "written_character": written}
                               for i in representative_order[:5]]
            group = {"id": group_id, "family": family, "label": f"Group {serial}",
                     "count": len(members), "written_character": written,
                     "status": "proposed" if written else "unassigned",
                     "coherence": round(float(1 - distances.mean()), 5),
                     "assignment_enabled": assignment_enabled,
                     "production_counts": dict(Counter(m.get("production", "unknown") for m in members)),
                     "source_counts": dict(Counter(m["corpus"] for m in members)),
                     "representatives": representatives, "anchors": votes,
                     "model_revision": revision}
            groups.append(group)
            head_groups.append({k: group[k] for k in ("id", "family", "written_character", "status", "assignment_enabled")})
            head_centroids.append(centers[c].cpu().numpy())
            head_radii.append(radius)
            for local_index, member in zip(local, members, strict=True):
                similarity = float(x[local_index] @ centers[c])
                scores = x[local_index] @ centers.T
                others = scores[torch.arange(len(scores), device=x.device) != c]
                margin = similarity - float(others.max()) if len(others) else 1.0
                assignment = (written if assignment_enabled and 1 - similarity <= radius
                              and margin >= .025 else None)
                anchor = anchors.get(member["id"])
                if anchor is not None:
                    assignment = anchor.get("character")
                if assignment and assignment not in member_characters:
                    raise ValueError("An inspected character is outside the registered family")
                output["assignments"][member["id"]] = {
                    "id": member["id"], "corpus": member["corpus"], "family": family,
                    "source_label": member["source_label"], "source_code_point": member.get("source_code_point"),
                    "crop_sha256": member["crop_sha256"], "source_revision": member.get("source_revision"),
                    "source_signature": member.get("source_signature"),
                    "written_character": assignment, "identity_status": "assigned" if assignment else "unassigned",
                    "identity_basis": "visual_model", "verified": False, "model_revision": revision,
                    "assignment_method": "visual_inspection" if anchor else "visual_cluster",
                    "inspection": anchor,
                    "similarity": round(similarity, 6), "margin": round(margin, 6),
                    "visual_group": {k: group[k] for k in ("id", "label", "coherence", "status", "written_character", "model_revision")},
                }
        assigned = sum(bool(output["assignments"][rows[i]["id"]]["written_character"]) for i in indexes)
        for group in groups:
            for representative in group["representatives"]:
                representative["written_character"] = output["assignments"][representative["id"]]["written_character"]
        output["families"][family] = {"family": family, "members": rows[indexes[0]]["members"],
            "sample_count": len(indexes), "assigned_count": assigned,
            "unassigned_count": len(indexes) - assigned, "silhouette": round(score, 5),
            "groups": groups}
    if (hashlib.sha256((root / "samples.jsonl").read_bytes()).hexdigest() != manifest_sha
            or hashlib.sha256((root / "embeddings.npy").read_bytes()).hexdigest() != embedding_sha):
        raise ValueError("Sample data changed during grouping; publication withheld")
    from glyph_atlas.visual_aliases import alias_assignments
    duplicates = root / "duplicate-sources.json"
    if duplicates.is_file():
        output["assignments"].update(alias_assignments(output["assignments"], json.loads(duplicates.read_text())))
    atomic_json(root / "classifier.json", {"version": 1, "model_revision": revision,
        "encoder_sha256": encoder_sha,
        "checkpoint_sha256": metadata.get("checkpoint_sha256"),
        "parity_sha256": hashlib.sha256((root / "onnx-parity.json").read_bytes()).hexdigest(),
        "groups": head_groups, "training_targets": "visual clusters with explicit anchors",
        "centroids": np.stack(head_centroids).tolist(), "radii": head_radii,
        "calibrated_probabilities": False, "manifest_sha256": manifest_sha,
        "embedding_sha256": embedding_sha})
    atomic_json(root / "assignments.json", output)
    print(json.dumps({"families": len(families), "samples": len(rows), "groups": len(head_groups),
                      "assigned": sum(bool(r["written_character"]) for r in output["assignments"].values()),
                      "revision": revision}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("work/visual-families"))
    parser.add_argument("--anchors", type=Path)
    args = parser.parse_args()
    run(args.directory, args.anchors)
