import json
from pathlib import Path
import subprocess
import sys
import tempfile

from grokteam import bundle
from grokteam.engine import Engine
from grokteam.release import metadata
from grokteam.store import TeamError, digest
from examples.human_fixture import grant
from test_runtime import ROOT, RuntimeCase


WORDING = bundle.texts(ROOT)
CARD = WORDING['card']
YES = WORDING['buttons']['yes']
NO = WORDING['buttons']['no']


class ChatConfirmationTests(RuntimeCase):
    def plan(self):
        return self.engine.confirmation_describe("policy.set", "system", {"mode": "autonomous"})

    def chat(self, plan, *, asked=None, answer=YES, ref="chat-1", ttl=300):
        self.engine.actor = "lead"
        return self.engine.confirmation_chat(plan["action"], plan["target"], plan["hash"], ref,
                                             answer, plan["question"] if asked is None else asked, ttl=ttl)

    def test_chat_cli_and_audit_keep_exact_dialogue(self):
        base = [sys.executable, "-B", str(ROOT / "teamctl.py"), "--workspace", str(self.engine.store.root)]
        def cli(*args):
            result = subprocess.run(base + list(args), capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        params = self.base / "params.json"
        params.write_text(json.dumps({"mode": "autonomous"}))
        plan = cli("confirm", "describe", "--action", "policy.set", "--target", "system", "--file", str(params))
        self.assertIn(WORDING["questions"]["policy.set"], plan["question"])
        self.assertEqual(plan["buttons"], {"yes": YES, "no": NO})
        self.assertEqual(plan["question"].splitlines()[0], CARD["question"].format(action=WORDING["questions"]["policy.set"], subject="system"))
        self.assertIn("autonomous", plan["question"])
        code = cli("confirm", "chat", "--action", "policy.set", "--target", "system", "--hash", plan["hash"],
                   "--asked", plan["question"], "--answer", YES, "--message-ref", "real-chat-1")
        result = cli("--actor", "user", "--confirmation", code["code"], "policy", "set", "autonomous",
                     "--evidence", "real-chat-1")
        self.assertEqual(result["policy"]["system_mode"], "autonomous")
        event = next(e["data"] for e in self.engine.store.events("system") if e["kind"] == "confirmation.issued")
        self.assertEqual({k: event[k] for k in ("message_ref", "asked", "answer")},
                         {"message_ref": "real-chat-1", "asked": plan["question"], "answer": YES})
        self.assertNotIn(code["code"], json.dumps(self.engine.store.export()))

    def test_chat_cli_accepts_json_file_and_stdin(self):
        plan = self.plan()
        command = [sys.executable, "-B", str(ROOT / "teamctl.py"), "--workspace", str(self.engine.store.root),
                   "--actor", "lead", "confirm", "chat", "--action", "policy.set", "--target", "system",
                   "--hash", plan["hash"]]
        path = self.base / "chat.json"
        payload = {"asked": plan["question"], "answer": YES, "message_ref": "json-file"}
        path.write_text(json.dumps(payload))
        result = subprocess.run(command + ["--file", str(path)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["channel"], "chat")
        payload["message_ref"] = "json-stdin"
        result = subprocess.run(command + ["--file", "-"], input=json.dumps(payload), capture_output=True,
                                text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["channel"], "chat")
        result = subprocess.run(command + ["--file", "-", "--answer", YES], input=json.dumps(payload),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not both", result.stderr)
        result = subprocess.run(command + ["--file", "-"], input=json.dumps({'answer': YES}), capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires asked, answer and message_ref", result.stderr)
        audit = [event["data"] for event in self.engine.store.events("system") if event["kind"] == "confirmation.issued"]
        self.assertEqual({event["message_ref"] for event in audit}, {"json-file", "json-stdin"})
        self.assertTrue(all(event["asked"] == plan["question"] for event in audit))

    def test_only_lead_and_installer_can_register_chat_consent(self):
        plan = self.plan()
        arguments = (plan["action"], plan["target"], plan["hash"], "actor-check", YES, plan["question"])
        for actor in ("developer", "reviewer", "user", "controller"):
            with self.subTest(actor=actor), self.assertRaises(TeamError):
                self.as_role(actor)
                self.engine.confirmation_chat(*arguments)
        self.engine.actor = "installer"
        self.assertEqual(self.engine.confirmation_chat(*arguments)["channel"], "chat")

    def test_install_cli_defaults_to_approval(self):
        command = [sys.executable, "-B", str(ROOT / "teamctl.py"), "--workspace", str(self.base / "new-install"),
                   "--actor", "installer"]
        install = subprocess.run(command + ["install", "--mock", "--lead-id", "cli-default"], capture_output=True,
                                 text=True, check=True)
        self.assertEqual(json.loads(install.stdout)["status"], "ready")
        show = subprocess.run(command + ["policy", "show"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(show.stdout)["policy"]["system_mode"], "approval")

    def test_legacy_policy_without_confirmation_channel_defaults_to_chat(self):
        e = self.engine
        with e.store.transaction():
            old = e.policy()
            del old["confirmation_channel"]
            e.store.put("config", "policy", old)
        self.assertEqual(e.policy()["confirmation_channel"], "chat")
        self.assertEqual(self.chat(self.plan())["channel"], "chat")

    def test_question_contains_consequences(self):
        self.assertIn(CARD["consequences"].format(details=WORDING["consequences"]["policy.set"]),
                      self.plan()["question"].splitlines())

    def test_long_diff_preview_has_forty_lines_and_links_to_full_diff(self):
        exp = self.propose(extra="\n".join(f"marker-{n:02}" for n in range(80)))
        plan = self.engine.confirmation_describe("improve.approve", exp["id"])
        self.assertEqual(plan["diff_hash"], digest(plan["diff"]))
        self.assertGreater(len(plan["diff"].splitlines()), 40)
        preview, suffix = plan["question"].split(CARD["changes"] + "\n", 1)[1].rsplit("\n" + CARD["counter"].split("{shown}", 1)[0], 1)
        self.assertEqual(preview.splitlines(), plan["diff"].splitlines()[:40])
        self.assertTrue(suffix.startswith(CARD["counter"].format(shown=40, total=len(plan["diff"].splitlines())).split(" ", 1)[1] + ".\n"))
        self.assertIn(CARD["diff"].format(target=exp["id"]), suffix)
        cli = [sys.executable, "-B", str(ROOT / "teamctl.py"), "--workspace", str(self.engine.store.root), "diff"]
        for target in ([exp["id"]], [exp["baseline"], exp["candidate"]]):
            result = subprocess.run(cli + target, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)["diff"], plan["diff"])

    def test_diff_preview_bounds_even_one_long_line(self):
        exp = self.propose(extra="x" * 48_000)
        plan = self.engine.confirmation_describe("improve.approve", exp["id"])
        self.assertGreater(len(plan["diff"]), 48_000)
        self.assertLess(len(plan["question"]), 2500)
        self.assertEqual(plan["diff_hash"], digest(plan["diff"]))
        preview, suffix = plan["question"].split(CARD["changes"] + "\n", 1)[1].rsplit("\n" + CARD["counter"].split("{shown}", 1)[0], 1)
        self.assertTrue(suffix.startswith(CARD["counter"].format(shown=len(preview.splitlines()), total=len(plan["diff"].splitlines())).split(" ", 1)[1]
                                          + CARD["line_truncated"] + ".\n"))
        self.assertIn(CARD["diff"].format(target=exp["id"]), suffix)

    def test_question_requires_exact_description_and_hash(self):
        plan = self.plan()
        short = plan["hash"][:8]
        for asked in (plan["question"].replace(short, ""),
                      plan["question"].replace(short, "0" * 8),
                      "Another request\n" + CARD["hash"].format(hash=short)):
            with self.subTest(asked=asked):
                with self.assertRaisesRegex(TeamError, "Asked text"):
                    self.chat(plan, asked=asked)

    def test_negative_empty_and_ambiguous_answers_rejected(self):
        plan = self.plan()
        for answer in (NO, "", YES + "?", "yes/no", "maybe"):
            with self.subTest(answer=answer):
                with self.assertRaisesRegex(TeamError, "affirmative"):
                    self.chat(plan, answer=answer)
        self.assertEqual(self.chat(plan, answer=" " + YES.upper() + " ")["channel"], "chat")

    def test_selected_language_controls_chat_vocabulary(self):
        edition = metadata(ROOT)["language"]
        allowed = {"en": {"yes", "approve", "confirm", "yes, i confirm"},
                   "ru": {"да", "подтверждаю", "да, подтверждаю", "approve", "yes"}}[edition]
        self.assertEqual(set(WORDING["chat_answers"]), allowed)
        for answer in allowed:
            self.assertEqual(self.chat(self.plan(), answer=answer, ref=answer)["channel"], "chat")
        foreign = "да" if edition == "en" else "confirm"
        with self.assertRaisesRegex(TeamError, "affirmative"):
            self.chat(self.plan(), answer=foreign)

    def test_bridge_uses_edition_labels(self):
        operations = self.engine.store.all("operation")
        names = {op["target"]: op["data"]["name"].split(" · ")[0] for op in operations if op["kind"] == "create"}
        self.assertEqual(names, WORDING["roles"])

    def test_chat_grant_wrong_action_expiration_and_replay(self):
        e = self.engine
        expired = self.chat(self.plan(), ttl=1)
        e.actor, e.confirmation_code = "user", expired["code"]
        with e.store.transaction():
            with self.assertRaisesRegex(TeamError, "another action"):
                e.require_confirmation("service.stop", "service")
        e.clock = lambda: expired["expires_at"]
        with self.assertRaisesRegex(TeamError, "expired"):
            e.policy_set("autonomous", evidence="chat-1")
        issued = self.chat(self.plan(), ref="chat-2")
        e.actor, e.confirmation_code = "user", issued["code"]
        e.policy_set("autonomous", evidence="chat-2")
        with self.assertRaisesRegex(TeamError, "already used"):
            e.policy_set("autonomous", evidence="chat-2")
        with self.assertRaisesRegex(TeamError, "response already used"):
            self.chat(self.plan(), ref="chat-2")

    def test_chat_disabled_in_terminal_mode(self):
        self.engine.actor = "lead"
        self.engine.policy_channel_set("terminal")
        with self.assertRaisesRegex(TeamError, "disabled in terminal"):
            self.chat(self.plan())

    def test_switch_to_terminal_is_free_return_requires_terminal_grant(self):
        e = self.engine
        self.assertEqual(e.policy()["confirmation_channel"], "chat")
        code = self.chat(self.plan())["code"]
        self.assertEqual(e.policy_channel_set("terminal")["confirmation_channel"], "terminal")
        e.actor, e.confirmation_code = "user", code
        with self.assertRaisesRegex(TeamError, "disabled in terminal"):
            e.policy_set("autonomous", evidence="chat-1")
        e.confirmation_code = None
        with self.assertRaisesRegex(TeamError, "confirmation code required"):
            e.policy_channel_set("chat")
        grant(e, "policy.channel", "system", {"channel": "chat"})
        self.assertEqual(e.policy_channel_set("chat")["confirmation_channel"], "chat")

    def test_install_autonomous_requires_grant_and_default_is_approval(self):
        for lead_id, mode in (("bot-default", "approval"), ("bot-experimental", "autonomous")):
            e = Engine(self.base / lead_id, actor="installer")
            try:
                if mode == "autonomous":
                    with self.assertRaisesRegex(TeamError, "confirmation code required"):
                        e.install(lead_id, mode=mode)
                    self.assertIsNone(e.store.get("config", "team"))
                    plan = e.confirmation_describe("install", lead_id,
                        {"mode": mode, "adapter": "native", "source": bundle.repository(ROOT)})
                    e.confirmation_code = e.confirmation_chat("install", lead_id, plan["hash"], "setup-1",
                                                              YES, plan["question"])["code"]
                    team = e.install(lead_id, mode=mode)
                    started = e.store.events(team["id"])[-1]["data"]
                    self.assertEqual(started["mode"], "autonomous")
                    self.assertTrue(started["confirmation"]["code_hash"])
                else:
                    e.install(lead_id)
                self.assertEqual(e.policy()["system_mode"], mode)
                self.assertEqual(e.policy()["confirmation_channel"], "chat")
            finally:
                e.close()

    def test_final_acceptance_requires_its_own_grant(self):
        e = self.engine
        exp = self.evaluated()
        plan = e.confirmation_describe("improve.approve", exp["id"])
        self.assertIn(CARD["changes"], plan["question"])
        self.assertIn("PRESERVE_NUMBERS", plan["question"])
        self.assertIn(plan["diff"].rstrip("\n"), plan["question"])
        self.assertIn(f"`diff {exp['id']}`", plan["question"])
        self.assertNotIn(CARD["counter"].split("{shown}", 1)[0], plan["question"])
        approved = self.chat(plan)
        e.actor, e.confirmation_code = "user", approved["code"]
        e.improve_approve(exp["id"], "chat-1")
        e.actor = "reviewer"
        for n in range(2):
            waiting = e.improve_observe(exp["id"], True, False, f"observation-{n}")
        self.assertEqual(waiting["status"], "trial")
        self.assertTrue(waiting["awaiting_acceptance"])
        with self.assertRaises(TeamError):
            e.improve_accept(exp["id"], "reviewer's decision")
        e.actor, e.confirmation_code = "user", None
        with self.assertRaisesRegex(TeamError, "confirmation code required"):
            e.improve_accept(exp["id"], "human said yes")
        final = self.chat(e.confirmation_describe("improve.accept", exp["id"]), ref="chat-2")
        e.actor, e.confirmation_code = "user", final["code"]
        self.assertEqual(e.improve_accept(exp["id"], "chat-2")["status"], "accepted")

    def test_trial_waiting_for_human_can_still_expire(self):
        e = self.engine
        exp = self.evaluated()
        self.as_role("user").improve_approve(exp["id"], "initial approval")
        e.actor = "reviewer"
        for n in range(2):
            exp = e.improve_observe(exp["id"], True, False, f"observation-{n}")
        e.clock = lambda: exp["deadline"]
        e.actor = "controller"
        self.assertEqual(e.sweep()[0]["status"], "rolled_back")

    def test_report_is_gitignored_and_documents_tty_limit(self):
        local_report = "FIX-" + "REPORT.md"
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / ".gitignore").write_text((ROOT / ".gitignore").read_text())
            result = subprocess.run(["git", "check-ignore", local_report], cwd=repo, capture_output=True)
            self.assertEqual(result.returncode, 0)
        bilingual = metadata(ROOT)["language"] == "en"
        readme = (ROOT / ("README.ru.md" if bilingual else "README.md")).read_text()
        self.assertIn("в штатном пути", readme.casefold())
        self.assertIn("(SECURITY.ru.md)" if bilingual else "(SECURITY.md)", readme)
        if bilingual:
            english = (ROOT / "README.md").read_text()
            self.assertIn("in the normal path", english.casefold())
            self.assertIn("(SECURITY.md)", english)
        previous = ROOT / local_report
        if previous.is_file():  # Local audit report is intentionally omitted from archives.
            report = previous.read_text()
            self.assertIn("псевдотерминал её проходит", report)
            self.assertNotIn("issuer отделён от обычного вызова CLI", report)
