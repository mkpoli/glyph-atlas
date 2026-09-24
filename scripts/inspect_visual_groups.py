"""Create audit sheets of central, boundary, and random members of each group."""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


def sheets(root):
    rows = {row["id"]: row for line in (root / "samples.jsonl").read_text().splitlines()
            if (row := json.loads(line))}
    registry = json.loads((root / "assignments.json").read_text())
    destination = root / "inspection"
    destination.mkdir(exist_ok=True)
    font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 17)
    inspections = []
    for family, summary in registry["families"].items():
        for group in summary["groups"]:
            members = [a for a in registry["assignments"].values() if a["visual_group"]["id"] == group["id"]]
            ordered = sorted(members, key=lambda a: -a["similarity"])
            rng = np.random.default_rng(17)
            random = [ordered[int(i)] for i in rng.permutation(len(ordered))]
            chosen = []
            for role, candidates in [("central", ordered[:4]), ("boundary", ordered[-4:]), ("random", random)]:
                for row in candidates:
                    if row["id"] not in [r["id"] for r in chosen]:
                        chosen.append({**row, "role": role})
                    if len(chosen) == 12:
                        break
                if len(chosen) == 12:
                    break
            page = Image.new("RGB", (1200, 740), "#e9e7e1")
            draw = ImageDraw.Draw(page)
            title = f"{family} {group['label']} | n={group['count']} | similarity={group['coherence']}"
            draw.text((14, 8), title, font=font, fill="black")
            for i, entry in enumerate(chosen):
                row = rows[entry["id"]]
                with Image.open(root / row["image_path"]) as image:
                    crop = ImageOps.contain(image.convert("RGB"), (176, 265))
                x, y = (i % 6) * 200, (i // 6) * 345 + 48
                page.paste(crop, (x + (200-crop.width)//2, y + (265-crop.height)//2))
                draw.text((x+8, y+272), f"{i+1} {entry['role']} {entry['similarity']:.3f}", font=font, fill="black")
                draw.text((x+8, y+297), row["corpus"], font=font, fill="#444444")
            filename = family.replace("+", "") + "-" + group["label"].replace(" ", "") + ".jpg"
            page.save(destination / filename, quality=92)
            inspections.append({"family": family, "group": group["id"], "sheet": filename,
                                "members": [{"position": i+1, "id": r["id"], "role": r["role"]}
                                            for i, r in enumerate(chosen)]})
    (destination / "index.json").write_text(json.dumps(inspections, ensure_ascii=False, indent=2))
    print(json.dumps({"sheets": len(inspections)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("work/visual-families"))
    sheets(parser.parse_args().directory)
