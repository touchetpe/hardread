"""C10 integration coverage: exact offered-list identity from the app route into the worker."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pipeline as P
import server as S
import explain_worker


REVIEW = {
    "meta": {"side": "p1", "end_reason": "ko", "end_turn": 3, "engine": "synthetic",
             "seconds": 0, "caveats": []},
    "bridge": {"version": 1, "S": 0.5, "L": 0.0, "R": 0.0, "C": 0.0,
               "s": 50, "l": 0, "c": 0, "result": "win", "swingy": False, "end_tag": None},
    "summary": {"result": "win", "start_win_prob": 0.5, "total_luck": 0.0,
                "decisions": 2, "decisions_with_hidden_action": 0,
                "win_prob_curve": [[0, 0.5], [2, 0.55], [3, 0.45]]},
    "turns": [
        {"turn": 2, "win_prob": 0.55, "action": "swordsdance", "opponent_action": "shadowball",
         "luck": 0.0, "luck_events": {}, "luck_realised_prob": 1.0},
        {"turn": 3, "win_prob": 0.45, "action": "headlongrush", "opponent_action": "recover",
         "luck": 0.0, "luck_events": {}, "luck_realised_prob": 1.0},
    ],
    "decisions": [
        {"turn": 2, "k": 0, "kind": "move", "action": "swordsdance", "win_prob": 0.55,
         "engine_best": "knockoff", "engine_best_win_prob": 0.64, "chosen_win_prob": 0.55,
         "decision_cost": 0.09, "top_alternatives": [],
         "opponent_policy_top": [["shadowball", 0.7], ["switch pikachu", 0.3]]},
        {"turn": 3, "k": 0, "kind": "move", "action": "headlongrush", "win_prob": 0.45,
         "engine_best": "rapidspin", "engine_best_win_prob": 0.53, "chosen_win_prob": 0.45,
         "decision_cost": 0.08, "top_alternatives": [],
         "opponent_policy_top": [["recover", 0.8], ["shadowball", 0.2]]},
    ],
}
CTX = {
    "mode": "draft", "side": "p1", "end_reason": "ko", "turns": 3, "winner": "p1",
    "names": {}, "draft_rows": [{"species_preview": "Great Tusk", "species": "Great Tusk",
                                  "moves": ["swordsdance", "knockoff"], "seen": True}],
    "active": {"2:0": "Great Tusk", "3:0": "Iron Valiant"}, "opp_active": {},
    "private": False, "own_species": ["Great Tusk"], "opp_species": ["Pikachu"],
}


class FakeJobs:
    def __init__(self, *args, **kwargs):
        self.files = None
        self.key = None

    def existing(self, _key):
        return None

    def allow(self, *_args):
        return True, 0

    def submit(self, _kind, key, files, _cmd, _on_done):
        self.key, self.files = key, dict(files)
        return "synthetic-job"


class RequestBody:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
        self.client = SimpleNamespace(host="127.0.0.1")

    async def body(self):
        return self.payload


class OfferedListIntegrationTests(unittest.TestCase):
    def test_ui_route_job_and_worker_keep_exact_nonempty_list(self):
        view, offered = P.build_view_with_offered(REVIEW, CTX)
        expected_keys = [{"turn": d["turn"], "k": d["k"]} for d in offered]
        self.assertEqual(expected_keys, [{"turn": 2, "k": 0}])
        self.assertEqual([(d["turn"], d["k"]) for d in offered],
                         [(d["turn"], d["k"]) for d in REVIEW["decisions"] if d["turn"] == 2])
        self.assertEqual([(d["turn"], d["k"]) for d in view["disagreements"]],
                         [(d["turn"], d["k"]) for d in expected_keys])

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            app = None
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs), \
                 patch.object(S, "EXPLAIN_MODE", "loop"), \
                 patch.object(S, "load_parts", return_value=(REVIEW, CTX, {"private": False})), \
                 patch.object(S, "verdicts_of", return_value={}), \
                 patch.object(S, "explain_store", return_value=root / "cache" / "explain.json"), \
                 patch.object(S, "explain_key_store", return_value=root / "cache" / "explain-keys.json"):
                app = S.create_app()
                endpoint = next(r.endpoint for r in app.routes
                                if getattr(r, "path", None) == "/api/explain" and "POST" in getattr(r, "methods", set()))
                response = asyncio.run(endpoint(RequestBody({"review_id": "synthetic", "question": "q3",
                                                              "offered_keys": expected_keys})))
                self.assertEqual(response["job_id"], "synthetic-job")
                jobs = app.state.jobs
                self.assertEqual(json.loads(jobs.files["offered.json"]), offered)
                self.assertEqual(json.loads(jobs.files["offered_keys.json"]), expected_keys)

                jobdir = root / "job"
                jobdir.mkdir()
                for name, content in jobs.files.items():
                    (jobdir / name).write_text(content)
                with patch.object(sys, "argv", ["explain_worker.py", str(jobdir), "--mode", "template"]):
                    self.assertEqual(explain_worker.main(), 0)
                result = json.loads((jobdir / "explain.json").read_text())
                dis_call = next(x for x in result["trace"]
                                if x["name"] == "list_key_moments" and x["args"].get("kind") == "disagreement")
                self.assertEqual([d["turn"] for d in dis_call["output"]["moments"]],
                                 [d["turn"] for d in expected_keys])

    def test_explicit_empty_list_survives_job_and_suppresses_assumed_decisions(self):
        hidden_ctx = dict(CTX, active={"2:0": "Iron Valiant", "3:0": "Iron Valiant"})
        view, offered = P.build_view_with_offered(REVIEW, hidden_ctx)
        self.assertEqual(offered, [])
        self.assertEqual(view["disagreements"], [])
        S._agent("explain")
        import tools as agent_tools
        self.assertEqual(agent_tools.ToolSession(REVIEW, offered=offered).disagreements(), [])
        templated = sys.modules["explain"].run(REVIEW, "Where did the engine disagree most with my choices?",
                                                  mode="template", offered=offered)
        moment_call = next(x for x in templated["trace"]
                           if x["name"] == "list_key_moments" and x["args"].get("kind") == "disagreement")
        self.assertEqual(moment_call["output"]["moments"], [])
        self.assertEqual(templated["rendered"]["text"], "This review shows no disagreements that met its display rules.")
        self.assertNotRegex(templated["rendered"]["text"], r"(?i)agreed|did not prefer|never rated|another option")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs), \
                 patch.object(S, "EXPLAIN_MODE", "loop"), \
                 patch.object(S, "load_parts", return_value=(REVIEW, hidden_ctx, {"private": False})), \
                 patch.object(S, "verdicts_of", return_value={}), \
                 patch.object(S, "explain_store", return_value=root / "cache" / "explain.json"), \
                 patch.object(S, "explain_key_store", return_value=root / "cache" / "explain-keys.json"):
                app = S.create_app()
                endpoint = next(r.endpoint for r in app.routes
                                if getattr(r, "path", None) == "/api/explain" and "POST" in getattr(r, "methods", set()))
                response = asyncio.run(endpoint(RequestBody({"review_id": "synthetic", "question": "q3",
                                                              "offered_keys": []})))
                self.assertEqual(response["job_id"], "synthetic-job")
                self.assertEqual(json.loads(app.state.jobs.files["offered.json"]), [])
                self.assertEqual(json.loads(app.state.jobs.files["offered_keys.json"]), [])
                jobdir = root / "job"
                jobdir.mkdir()
                for name, content in app.state.jobs.files.items():
                    (jobdir / name).write_text(content)
                with patch.object(sys, "argv", ["explain_worker.py", str(jobdir), "--mode", "template"]):
                    self.assertEqual(explain_worker.main(), 0)
                result = json.loads((jobdir / "explain.json").read_text())
                empty_call = next(x for x in result["trace"]
                                  if x["name"] == "list_key_moments" and x["args"].get("kind") == "disagreement")
                self.assertEqual(empty_call["output"]["moments"], [])

    def test_stale_browser_keys_are_rejected_before_job_submission(self):
        with patch.object(S, "CACHE", Path(tempfile.gettempdir()) / "hardread-c10-cache"), \
             patch.object(S, "JOBS", Path(tempfile.gettempdir()) / "hardread-c10-jobs"), \
             patch.object(S, "STATE", Path(tempfile.gettempdir()) / "hardread-c10-state"), \
             patch.object(S, "Jobs", FakeJobs), patch.object(S, "EXPLAIN_MODE", "loop"), \
             patch.object(S, "load_parts", return_value=(REVIEW, CTX, {"private": False})):
            app = S.create_app()
            endpoint = next(r.endpoint for r in app.routes
                            if getattr(r, "path", None) == "/api/explain" and "POST" in getattr(r, "methods", set()))
            response = asyncio.run(endpoint(RequestBody({"review_id": "synthetic", "question": "q3",
                                                          "offered_keys": []})))
            self.assertEqual(response.status_code, 409)
            self.assertIsNone(app.state.jobs.files)

    def test_read_check_status_is_parser_status(self):
        belief = {"expects_action": {"kind": "move", "move": "shadowball"}}
        checked = P.read_check(REVIEW, 2, 0, belief)
        self.assertEqual(checked["status"], "checked")
        no_choice = json.loads(json.dumps(REVIEW))
        no_choice["decisions"][0]["opponent_policy_top"] = [["", 1.0]]
        missing_move = P.read_check(no_choice, 2, 0, belief)
        self.assertEqual(missing_move["status"], "no_opponent_choice")


    def test_old_cache_is_withheld_current_cache_reused_and_verdicts_scoped(self):
        view, offered = P.build_view_with_offered(REVIEW, CTX)
        keys = [{"turn": d["turn"], "k": d.get("k", 0)} for d in offered]
        self.assertEqual(keys, [{"turn": 2, "k": 0}])
        hidden_verdict = {"verdict": "hidden fixture", "engine_best": "rapidspin", "delta_U": [0.08]}
        visible_verdict = {"verdict": "visible fixture", "engine_best": "knockoff", "delta_U": [0.04]}
        verdict_map = {"2:0": visible_verdict, "3:0": hidden_verdict}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store, key_store = root / "explain.json", root / "explain-keys.json"
            old_explanation = {"rendered": {"text": "legacy cached"}}
            store.write_text(json.dumps({"q3": old_explanation}))
            key_store.write_text(json.dumps({"q3": keys}))
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs), \
                 patch.object(S, "EXPLAIN_MODE", "loop"), \
                 patch.object(S, "load_parts", return_value=(REVIEW, CTX, {"private": False})), \
                 patch.object(S, "verdicts_of", return_value={}) as verdict_source, \
                 patch.object(S, "explain_store", return_value=store), \
                 patch.object(S, "explain_key_store", return_value=key_store), \
                 patch.object(S, "_template_explanation", return_value={"final": "template"}) as template:
                current_view = S.load_view("synthetic")
                self.assertEqual(current_view["explanations"], {})
                app = S.create_app()
                verdict_source.return_value = verdict_map
                endpoint = next(r.endpoint for r in app.routes
                                if getattr(r, "path", None) == "/api/explain" and "POST" in getattr(r, "methods", set()))
                request = RequestBody({"review_id": "synthetic", "question": "q3", "offered_keys": keys})
                response = asyncio.run(endpoint(request))
                self.assertEqual(response["job_id"], "synthetic-job")
                self.assertIn(S.EXPLANATION_POLICY_VERSION, app.state.jobs.key)
                self.assertEqual(json.loads(app.state.jobs.files["verdicts.json"]), {"2:0": visible_verdict})
                self.assertEqual(template.call_args.args[2], {"2:0": visible_verdict})

                current_explanation = {"rendered": {"text": "current version"},
                                       "policy_version": S.EXPLANATION_POLICY_VERSION}
                store.write_text(json.dumps({"q3": current_explanation}))
                S._save_explain_keys("synthetic", "q3", keys)
                reused = asyncio.run(endpoint(request))
                self.assertTrue(reused["cached"])
                verdict_source.return_value = {}
                self.assertEqual(S.load_view("synthetic")["explanations"]["q3"], current_explanation)


    def test_explain_job_verdict_filter_uses_exact_turn_and_k(self):
        offered_decision = dict(REVIEW["decisions"][0], k=1)
        exact_offered = [offered_decision]
        verdicts = {"2:0": {"verdict": "hidden key", "engine_best": "rapidspin"},
                    "2:1": {"verdict": "visible key", "engine_best": "knockoff"},
                    "3:0": {"verdict": "hidden turn", "engine_best": "shadowball"}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs), \
                 patch.object(S, "EXPLAIN_MODE", "loop"), \
                 patch.object(S, "load_parts", return_value=(REVIEW, CTX, {"private": False})), \
                 patch.object(P, "build_view_with_offered", return_value=({"disagreements": []}, exact_offered)), \
                 patch.object(S, "verdicts_of", return_value=verdicts), \
                 patch.object(S, "explain_store", return_value=root / "cache" / "explain.json"), \
                 patch.object(S, "explain_key_store", return_value=root / "cache" / "explain-keys.json"), \
                 patch.object(S, "_template_explanation", return_value={"final": "template"}):
                app = S.create_app()
                endpoint = next(r.endpoint for r in app.routes
                                if getattr(r, "path", None) == "/api/explain" and "POST" in getattr(r, "methods", set()))
                response = asyncio.run(endpoint(RequestBody({"review_id": "synthetic", "question": "q3",
                                                              "offered_keys": [{"turn": 2, "k": 1}]})))
                self.assertEqual(response["job_id"], "synthetic-job")
                self.assertEqual(json.loads(app.state.jobs.files["offered_keys.json"]), [{"turn": 2, "k": 1}])
                self.assertEqual(json.loads(app.state.jobs.files["verdicts.json"]), {"2:1": verdicts["2:1"]})


if __name__ == "__main__":
    unittest.main()
