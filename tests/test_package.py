"""Offline installation gate: installed assets must work without a source checkout."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_clean_venv_pip_install_contains_working_roles_skills_and_cli(self):
        with tempfile.TemporaryDirectory(prefix="grokteam-pip-gate-") as temp:
            root=Path(temp)
            environment=root/"venv"
            env={k:v for k,v in os.environ.items() if k not in ("PYTHONPATH","PYTHONHOME")}
            env.update(PIP_NO_CACHE_DIR="1",PIP_DISABLE_PIP_VERSION_CHECK="1",PIP_CONFIG_FILE=os.devnull,
                       PYTHONDONTWRITEBYTECODE="1")
            def command(argv,cwd=root):
                result=subprocess.run(argv,cwd=cwd,env=env,capture_output=True,text=True,timeout=120)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                return result.stdout
            command([sys.executable,"-m","venv",str(environment)])
            python=environment/"bin/python"
            command([str(python),"-m","pip","install","--no-index","--no-deps",str(ROOT)])
            cli=environment/"bin/grok-team"
            result=json.loads(command([str(cli),"--workspace",str(root/"state"),"--actor","installer", "install","--mock","--lead-id","pip-primary"]))
            self.assertEqual(result["status"],"ready")
            release = json.loads((ROOT / "release.json").read_text())
            self.assertEqual(result["release"]["channel"], release["channel"])
            self.assertIs(result["release"]["live_verified"], release["live_verified"])
            self.assertEqual(result["release"]["name"], release["name"])
            self.assertEqual(result["release"]["language"], release["language"])
            self.assertEqual(result["source"], release["repository"])
            distribution = command([str(python), "-c", "from importlib.metadata import version; import sys; print(version(sys.argv[1]))",
                                    release["distribution"]]).strip()
            self.assertEqual(distribution, release["version"])
            prefix = [str(cli), "--workspace", str(root / "state"), "confirm"]
            args = ["--action", "policy.set", "--target", "system"]
            params = root / "params.json"
            params.write_text('{"mode":"autonomous"}')
            plan = json.loads(command(prefix + ["describe", *args, "--file", str(params)]))
            response = {"asked": plan["question"], "answer": plan["buttons"]["yes"], "message_ref": "pip-check"}
            confirm = subprocess.run(prefix + ["chat", *args, "--hash", plan["hash"], "--file", "-"],
                                     cwd=root, env=env, input=json.dumps(response), capture_output=True,
                                     text=True, timeout=120)
            self.assertEqual(confirm.returncode, 0, confirm.stdout + confirm.stderr)
            self.assertEqual(json.loads(confirm.stdout)["channel"], "chat")
            role=json.loads(command([str(python),"-B","-m","grokteam","--workspace",str(root/"state"),"--actor","developer","role","read","developer"]))
            self.assertTrue(all(Path(skill["path"]).is_file() for skill in role["skills"]))
            self.assertTrue(all(str(environment) in skill["path"] for skill in role["skills"]))
            installed=json.loads(command([str(python),"-c","import json,grokteam; from grokteam import bundle; print(json.dumps({'module':grokteam.__file__,'release':str(bundle.ROOT),'verified':bundle.verify()}))"]))
            self.assertIn(str(environment),installed["module"])
            self.assertIn("_release",installed["release"])
            self.assertGreater(installed["verified"]["files"],40)

    def test_minimum_python_is_enforced_by_doctor_and_metadata(self):
        import contextlib,io,runpy
        from unittest import mock
        stdout=io.StringIO()
        with mock.patch.object(sys,"version_info",(3,9,99)),contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as error:
                runpy.run_path(str(ROOT/"scripts/doctor.py"),run_name="__main__")
        self.assertEqual(error.exception.code,2)
        self.assertFalse(json.loads(stdout.getvalue())["checks"]["python_3_10"])
        self.assertIn('requires-python = ">=3.10"',(ROOT/"pyproject.toml").read_text())

    def test_ci_has_read_only_permissions_and_commit_pins(self):
        text=(ROOT/".github/workflows/ci.yml").read_text()
        self.assertRegex(text,r"permissions:\s*\n\s+contents: read")
        refs=re.findall(r"uses:\s+actions/[^@\s]+@([^\s]+)",text)
        self.assertEqual(len(refs),2)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}",ref) for ref in refs))
        for version in ("3.10","3.11","3.12","3.13"):
            self.assertIn("'"+version+"'",text)


if __name__=="__main__": unittest.main()
