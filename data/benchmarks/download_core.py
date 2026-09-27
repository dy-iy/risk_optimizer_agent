"""Download pinned financial benchmarks using only Python's standard library.

Keep record values unchanged; format JSON arrays for local reading.
"""

import hashlib
import io
import json
from pathlib import Path
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
HEADERS = {"User-Agent": "financial-benchmark-research/1.0"}
FIN_REV = "3b686ca79e2184b36498ba15fd1e9609d9eda3cb"
FEW_REV = "ec6cac90238c04b45d4512a695ee6375fc72af2f"


def fetch(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main():
    previous_path = ROOT / "manifest.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    previous_files = {item["path"]: item for item in previous.get("files", [])}
    manifest = {
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": [],
        "datasets": {},
    }

    def save(relative, url, expected_size=None, expected_sha256=None):
        target = ROOT / relative
        prior = previous_files.get(relative, {})
        if target.exists() and "upstream_sha256" in prior:
            payload = target.read_bytes()
            if len(payload) != prior["bytes"] or hashlib.sha256(payload).hexdigest() != prior["sha256"]:
                raise ValueError(f"Local file differs from manifest: {relative}")
            if expected_size is not None and prior["upstream_bytes"] != expected_size:
                raise ValueError(f"Upstream size mismatch: {relative}")
            manifest["files"].append(prior)
            return payload
        payload = target.read_bytes() if target.exists() else fetch(url)
        digest = hashlib.sha256(payload).hexdigest()
        if expected_size is not None and len(payload) != expected_size:
            raise ValueError(f"Size mismatch: {relative}")
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError(f"SHA256 mismatch: {relative}")
        if payload.startswith(b"version https://git-lfs.github.com/spec"):
            raise ValueError(f"LFS pointer instead of data: {relative}")
        record = {"path": relative, "url": url, "bytes": len(payload), "sha256": digest}
        if relative.endswith(".json"):
            text = payload.decode("utf-8")
            try:
                records = json.loads(text)
            except json.JSONDecodeError:
                records = [json.loads(line) for line in text.splitlines() if line.strip()]
            payload = (json.dumps(records, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if hashlib.sha256(payload).hexdigest() != digest:
                record.update(upstream_bytes=record["bytes"], upstream_sha256=digest,
                              bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                              local_format="JSON array, UTF-8, indent=2; record values and order unchanged")
        if not relative.endswith(".zip"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            manifest["files"].append(record)
        else:
            manifest.setdefault("removed_download_artifacts", []).append(record)
        print(f"OK {relative}: {len(payload)} bytes", flush=True)
        return payload

    repo = "supersymmetry-technologies/BBT-FinCUGE-Applications"
    tree = json.loads(fetch(f"https://api.github.com/repos/{repo}/git/trees/{FIN_REV}?recursive=1"))
    for task in ("finnsp", "finnl"):
        manifest["datasets"][task] = {"repository": f"https://github.com/{repo}", "revision": FIN_REV}
        prefix = f"FinCUGE_Publish/{task}/"
        for item in tree["tree"]:
            if item["type"] == "blob" and item["path"].startswith(prefix):
                path = item["path"]
                save(f"{task}/raw/{path.removeprefix(prefix)}",
                     f"https://raw.githubusercontent.com/{repo}/{FIN_REV}/{path}", item["size"])
        save(f"{task}/UPSTREAM_README.md", f"https://raw.githubusercontent.com/{repo}/{FIN_REV}/README.md")

    repo = "TimeBurningFish/FewFC"
    manifest["datasets"]["fewfc"] = {
        "repository": f"https://github.com/{repo}", "revision": FEW_REV,
        "included": ["original", "rearranged", "README.md", "LICENSE.txt", "Event_Definition.pdf"],
        "excluded": "partial/ contains alternative derived argument-extraction experiments",
    }
    tree = json.loads(fetch(f"https://api.github.com/repos/{repo}/git/trees/{FEW_REV}?recursive=1"))
    for item in tree["tree"]:
        path = item["path"]
        if item["type"] == "blob" and (path.startswith(("original/", "rearranged/")) or path in ("README.md", "LICENSE.txt", "Event_Definition.pdf")):
            save(f"fewfc/{path}", f"https://raw.githubusercontent.com/{repo}/{FEW_REV}/{path}", item["size"])

    dataset = "takala/financial_phrasebank"
    # Once resolved, retain this revision on repeat runs.
    lock = ROOT / "phrasebank_revision.txt"
    if lock.exists():
        revision = lock.read_text(encoding="utf-8").strip()
    else:
        revision = json.loads(fetch(f"https://huggingface.co/api/datasets/{dataset}"))["sha"]
        lock.write_text(revision + "\n", encoding="utf-8")
    manifest["datasets"]["financial_phrasebank"] = {
        "repository": f"https://huggingface.co/datasets/{dataset}", "revision": revision,
    }
    base = f"https://huggingface.co/datasets/{dataset}/resolve/{revision}"
    save("financial_phrasebank/UPSTREAM_README.md", f"{base}/README.md")
    payload = save("financial_phrasebank/FinancialPhraseBank-v1.0.zip",
                   f"{base}/data/FinancialPhraseBank-v1.0.zip", 681890,
                   "0e1a06c4900fdae46091d031068601e3773ba067c7cecb5b0da1dcba5ce989a6")
    destination = (ROOT / "financial_phrasebank" / "raw").resolve()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for item in archive.infolist():
            if "__MACOSX" in Path(item.filename).parts or Path(item.filename).name.startswith("._"):
                continue
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("Unsafe archive path")
            if item.is_dir():
                continue
            data = archive.read(item)  # Also verifies CRC.
            if target.exists() and target.read_bytes() != data:
                raise ValueError(f"Existing extracted file differs: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            manifest["files"].append({
                "path": target.relative_to(ROOT).as_posix(),
                "archive_member": item.filename,
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            })
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Manifest written.", flush=True)


if __name__ == "__main__":
    main()
