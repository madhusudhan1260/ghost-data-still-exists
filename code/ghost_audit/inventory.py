"""Evidence inventory: load the manifest, parse every file once, cache observations."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from . import parsers
from .chain import load_activities
from .docs import catalog_from_activities, docs_to_obs
from .models import Obs
from .ocr import ocr_image


@dataclass
class EvidenceFile:
    file_id: str
    path: Path
    origin: str
    captured_at: str
    suffix: str


@dataclass
class Inventory:
    files: dict[str, EvidenceFile] = field(default_factory=dict)
    obs: list[Obs] = field(default_factory=list)
    docs: list[parsers.DocText] = field(default_factory=list)
    report: parsers.ParseReport = field(default_factory=parsers.ParseReport)


def load_inventory(dataset_dir: Path) -> Inventory:
    inv = Inventory()
    with open(dataset_dir / "evidence_files.csv", newline="") as f:
        for r in csv.DictReader(f):
            rel = r["file_path"]
            p = dataset_dir / rel
            inv.files[r["file_id"]] = EvidenceFile(r["file_id"], p, r["origin"], r["captured_at"], p.suffix.lower())
    for fid, ef in inv.files.items():
        try:
            if not ef.path.exists():
                raise FileNotFoundError(str(ef.path))
            if ef.suffix == ".sql":
                obs = parsers.parse_sql(ef.path, fid, ef.captured_at)
            elif ef.suffix == ".json":
                obs = parsers.parse_json(ef.path, fid, ef.captured_at)
            elif ef.suffix == ".log":
                obs = parsers.parse_log(ef.path, fid, ef.captured_at)
            elif ef.suffix == ".txt":
                obs = parsers.parse_text(ef.path, fid, ef.captured_at)
            elif ef.suffix == ".pdf":
                text = parsers.parse_pdf_text(ef.path)
                inv.docs.append(parsers.DocText(fid, ef.captured_at, text, "pdf"))
                obs = []
                inv.report.notes[fid] = f"pdf text chars={len(text)}"
            elif ef.suffix in (".png", ".jpg", ".jpeg"):
                text, engine = ocr_image(ef.path)
                inv.docs.append(parsers.DocText(fid, ef.captured_at, text, "image"))
                obs = []
                inv.report.notes[fid] = f"ocr[{engine}] chars={len(text)}"
            else:
                raise ValueError(f"unsupported evidence format {ef.suffix}")
            inv.obs.extend(obs)
            inv.report.parsed[fid] = len(obs)
        except Exception as e:  # noqa: BLE001 - recorded, surfaced in the run report
            inv.report.failures[fid] = f"{type(e).__name__}: {e}"
    # documents / screenshots: bind mentioned resources through the data catalog
    catalog = catalog_from_activities(load_activities(dataset_dir / "system_activity.csv"))
    doc_obs = docs_to_obs(inv.docs, catalog)
    inv.obs.extend(doc_obs)
    for o in doc_obs:
        inv.report.parsed[o.file_id] = inv.report.parsed.get(o.file_id, 0) + 1
    return inv
