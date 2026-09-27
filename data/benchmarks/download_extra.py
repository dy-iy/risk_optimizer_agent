"""Download official FinCausal 2020 files and EDT event-detection subset."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from download_core import fetch

ROOT = Path(__file__).resolve().parent
REV = "33a933f1bebd3317ab67edcf447c1a333fe879f5"


def main():
    manifest = {"downloaded_at_utc": datetime.now(timezone.utc).isoformat(), "files": [], "failures": []}

    def save(path, url, expected_size=None):
        target = ROOT / path
        data = target.read_bytes() if target.exists() else fetch(url)
        if expected_size is not None and len(data) != expected_size:
            raise ValueError(f"Size mismatch: {path}: {len(data)} != {expected_size}")
        if data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            raise ValueError("Received HTML instead of data")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
        manifest["files"].append({"path": path, "url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        print(f"OK {path}: {len(data)} bytes", flush=True)

    prefix = "FNP_2020_FinCausal/"
    tree = json.loads(fetch(f"https://api.github.com/repos/yseop/YseopLab/git/trees/{REV}?recursive=1"))
    for item in tree["tree"]:
        path = item["path"]
        if item["type"] == "blob" and (path.startswith(prefix + "data/") or path in (prefix + "AnnotationScheme.pdf", prefix + "README.adoc")):
            save("fincausal2020/" + path.removeprefix(prefix), f"https://raw.githubusercontent.com/yseop/YseopLab/{REV}/{path}", item["size"])

    # IDs and sizes from the public folder linked by the paper's authors.
    for filename, file_id, size in [
        ("dev.txt", "17qTLsFn0vwThy_gaZTYQ5ssgw1fYRKEw", 7417821),
        ("train.txt", "1eO3G6fcbRX-1Q5fEI_zghb3nYe7yejct", 29900186),
    ]:
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        try:
            save(f"edt/Event_detection/{filename}", url, size)
        except Exception as exc:
            manifest["failures"].append({"path": f"edt/Event_detection/{filename}", "url": url, "error": str(exc)})
            print(f"FAILED {filename}: {exc}", flush=True)
    save("edt/UPSTREAM_README.md", "https://raw.githubusercontent.com/Zhihan1996/TradeTheEvent/main/data/README.md")
    (ROOT / "manifest_extra.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
