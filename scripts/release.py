#!/usr/bin/env python3
"""Reproducible prerelease archives. Run from any directory."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from grokteam.release import source_files as files, render, check


def archive(root, out):
    root = Path(root)
    manifest = check(root)
    prefix = f'{manifest["name"]}-{manifest["version"]}'
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted([*manifest["files"], "MANIFEST.json"]):
            data = (root / name).read_bytes()
            item = tarfile.TarInfo(prefix + "/" + name)
            item.size, item.mtime, item.mode = len(data), 0, 0o644
            tar.addfile(item, io.BytesIO(data))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as target:
        with gzip.GzipFile(filename="", fileobj=target, mode="wb", mtime=0) as gz:
            gz.write(raw.getvalue())
    return hashlib.sha256(out.read_bytes()).hexdigest()


def zip_archive(root, out):
    root = Path(root)
    manifest = check(root)
    prefix = f'{manifest["name"]}-{manifest["version"]}'
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name in sorted([*manifest["files"], "MANIFEST.json"]):
            info = zipfile.ZipInfo(prefix + "/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type, info.external_attr = zipfile.ZIP_DEFLATED, 0o100644 << 16
            target.writestr(info, (root / name).read_bytes())
    return hashlib.sha256(out.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["manifest", "check", "build"])
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    root = a.root.resolve()
    try:
        if a.action == "manifest":
            value = render(root)
            (root / "MANIFEST.json").write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        elif a.action == "check":
            value = check(root)
        else:
            value = check(root)
            stem = f'{value["name"]}-{value["version"]}'
            out = a.out or root / "dist" / (stem + ".tar.gz")
            sha = archive(root, out)
            zipped = out.parent / (stem + ".zip")
            zip_sha = zip_archive(root, zipped)
            for path, checksum in ((out, sha), (zipped, zip_sha)):
                path.with_name(path.name + ".sha256").write_text(checksum + "  " + path.name + "\n")
            print(json.dumps({"archive": str(out), "sha256": sha, "zip": str(zipped), "zip_sha256": zip_sha}))
            return
        print(json.dumps({**value, "files": len(value["files"])}))
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
