"""Small PEP 517 backend: a complete, offline-installable stdlib-only wheel."""
import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile

from .release import check, metadata

ROOT = Path(__file__).resolve().parents[1]


def get_requires_for_build_wheel(config_settings=None):
    return []


def _metadata():
    release = metadata(ROOT)
    name, version = release["distribution"], release["version"]
    info = f"{name.replace('-', '_')}-{version}.dist-info"
    return info, {
        "METADATA": (f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
                     "Summary: Three-role development team, pilot prerelease\n"
                     "Requires-Python: >=3.10\nLicense: MIT\nAuthor: alexzah-droid\n\n"
                     "Unofficial project; not affiliated with xAI.\n").encode(),
        "WHEEL": b"Wheel-Version: 1.0\nGenerator: grokteam-stdlib-backend\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        "entry_points.txt": b"[console_scripts]\ngrok-team = grokteam.cli:main\n",
        "licenses/LICENSE": (ROOT / "LICENSE").read_bytes(),
    }


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    info, files = _metadata()
    for name, data in files.items():
        path = Path(metadata_directory) / info / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return info


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    manifest = check(ROOT)
    info, metadata_files = _metadata()
    content = {}
    for name in sorted([*manifest["files"], "MANIFEST.json"]):
        data = (ROOT / name).read_bytes()
        content["grokteam/_release/" + name] = data
        if name.startswith("grokteam/") and name.endswith(".py"):
            content[name] = data
    content.update({info + "/" + name: data for name, data in metadata_files.items()})
    records = []
    for name, data in sorted(content.items()):
        sha = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        records.append([name, "sha256=" + sha, str(len(data))])
    records.append([info + "/RECORD", "", ""])
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(records)
    content[info + "/RECORD"] = output.getvalue().encode()
    filename = info.removesuffix(".dist-info") + "-py3-none-any.whl"
    destination = Path(wheel_directory) / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for name, data in sorted(content.items()):
            item = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            item.compress_type, item.external_attr = zipfile.ZIP_DEFLATED, 0o100644 << 16
            wheel.writestr(item, data)
    return filename
