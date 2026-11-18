import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote

from grokteam import bundle
from grokteam.build_backend import _metadata
from grokteam.release import metadata
from grokteam.store import TeamError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_script", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_inventory_matches_actual_release(self):
        self.assertGreater(bundle.verify()["files"], 30)
        release.check(ROOT)

    def test_reproducible_archives_and_clean_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = release.archive(ROOT, root / "a.tar.gz")
            second = release.archive(ROOT, root / "b.tar.gz")
            self.assertEqual(first, second)
            self.assertEqual(release.zip_archive(ROOT, root / "a.zip"), release.zip_archive(ROOT, root / "b.zip"))
            import tarfile
            with tarfile.open(root / "a.tar.gz") as tar:
                names = tar.getnames()
            data = metadata(ROOT)
            prefix = f'{data["name"]}-{data["version"]}/'
            self.assertIn(prefix + "INSTALL.md", names)
            self.assertIn(prefix + "tests/test_runtime.py", names)
            self.assertTrue(all(name.startswith(prefix) for name in names))
            self.assertFalse(any(".runtime" in n or ".sqlite" in n or n.startswith("../") for n in names))

    def test_corruption_and_unlisted_files_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "release"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", ".runtime", "dist", "__pycache__"))
            bundle.verify(copy)
            (copy / "unlisted.py").write_text("unsafe")
            with self.assertRaises(TeamError): bundle.verify(copy)
            (copy / "unlisted.py").unlink()
            (copy / "bots/developer/behavior.md").write_text("corrupt")
            with self.assertRaises(TeamError): bundle.verify(copy)

    def test_all_local_documentation_links_resolve(self):
        for path in ROOT.rglob("*.md"):
            if any(x in ("dist", ".runtime", ".git") for x in path.relative_to(ROOT).parts): continue
            for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)', path.read_text()):
                if "://" in target:
                    continue
                destination, _, fragment = target.partition("#")
                resolved = path.parent / unquote(destination) if destination else path
                self.assertTrue(resolved.exists(), f"{path}: {target}")
                if fragment and resolved.suffix == ".md":
                    headings = re.findall(r"^#{1,6} +(.+)$", resolved.read_text(), re.M)
                    anchors = {re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
                               for heading in headings}
                    self.assertIn(unquote(fragment), anchors, f"{path}: {target}")

    def test_human_documentation_has_matching_language_pairs(self):
        if metadata(ROOT)["language"] == "ru":
            self.assertFalse(list(ROOT.rglob("*.ru.md")))
            self.assertFalse(list(ROOT.rglob("*.en.md")))
            return
        pairs = ("README", "SECURITY", "CHANGELOG", "docs/CONFIRMATIONS", "docs/OPERATIONS")
        for name in pairs:
            with self.subTest(page=name):
                en = (ROOT / (name + ".md")).read_text()
                ru = (ROOT / (name + ".ru.md")).read_text()
                basename = Path(name).name
                self.assertEqual(en.splitlines()[2], f"**English** · [Русский]({basename}.ru.md)")
                self.assertEqual(ru.splitlines()[2], f"[English]({basename}.md) · **Русский**")
                levels = re.compile(r"^(#{1,2}) +", re.M)
                self.assertEqual(levels.findall(en), levels.findall(ru))

    def test_current_release_descriptions_omit_installation_status_caveats(self):
        caveats = re.compile(
            r"жив(?:ая|ой)\s+установ|live\s+install|not\s+verified|unverifi(?:ed)"
            r"|не\s+проверена|предстоит\s+(?:подтвердить|проверить)"
            r"|still\s+needs\s+to\s+(?:verify|confirm)|live_verified",
            re.IGNORECASE,
        )
        descriptions = [*ROOT.glob("README*.md"), *ROOT.glob("SECURITY*.md"),
                        *ROOT.glob("VALIDATION.md"), *ROOT.glob("INSTALL.md"),
                        *ROOT.glob("install-prompt.txt"), *(ROOT / "docs").glob("*.md")]
        for path in descriptions:
            with self.subTest(page=path.relative_to(ROOT)):
                content = path.read_text(encoding="utf-8")
                self.assertNotRegex(content, caveats)
                if path.name.startswith("README"):
                    self.assertNotRegex(content, r"(?m)^\*\*(?:Status|Статус):\*\*")
        self.assertNotRegex(_metadata()[1]["METADATA"].decode("utf-8"), caveats)

    def test_local_maintenance_markdown_is_excluded_from_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ("FIX-" + "REPORT.md")).write_text("local")
            (root / "FIX-REVIEW.md").write_text("local")
            (root / "README.md").write_text("public")
            self.assertEqual(set(release.files(root)), {"README.md"})

    def test_six_skills_are_self_contained_and_role_dependencies_exist(self):
        registry = json.loads((ROOT / "skills/manifest.json").read_text())["skills"]
        self.assertEqual(len(registry), 6)
        names = {item["name"] for item in registry}
        for item in registry:
            path = ROOT / "skills" / item["name"] / "SKILL.md"
            self.assertTrue(path.is_file())
            self.assertIn("name: " + item["name"], path.read_text())
            for role in item["roles"]:
                assigned = {x["name"] for x in bundle.skills(role)}
                self.assertTrue(set(item["dependencies"]).issubset(assigned))
            self.assertTrue(set(item["dependencies"]).issubset(names))

    def test_cli_errors_are_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, "-B", str(ROOT / "teamctl.py"), "--workspace", tmp,
                                     "--actor", "developer", "policy", "set", "autonomous", "--evidence", "bot:yes"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("error", json.loads(result.stderr))


if __name__ == "__main__":
    unittest.main()
