"""C10 integration coverage: exact offered-list identity from the app route into the worker."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pipeline as P
import server as S
import explain_worker
import static_export


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
        self.on_done = None

    def existing(self, _key):
        return None

    def allow(self, *_args):
        return True, 0

    def submit(self, _kind, key, files, _cmd, _on_done):
        self.key, self.files, self.on_done = key, dict(files), _on_done
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
                S._save_explain_keys("synthetic", "q3", keys, "loop")
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


class TemplateGateTests(unittest.TestCase):
    def setUp(self):   # these tests cover the cache and fallback rules; saved showcase answers are tested on their own
        saved = patch.object(S, "SHOW_SAVED_ANSWERS", False)
        saved.start()
        self.addCleanup(saved.stop)

    def test_saved_showcase_answers_are_shown_and_labelled(self):
        with patch.object(S, "SHOW_SAVED_ANSWERS", True), patch.object(S, "_jsonfile", side_effect=lambda p, d=None: (
                {"q1": {"final": "glimmer_raw", "rendered": {"text": "saved"}}, "q2": {"final": "template"}}
                if str(p).endswith(".explain.json") else (REVIEW if str(p).endswith(".review.json") else
                (CTX if str(p).endswith(".ctx.json") else d)))):
            view = S.load_view("showcase-1")
        self.assertTrue(view["explanations"]["q1"]["precomputed"])
        self.assertNotIn("q2", view["explanations"])

    def test_unset_default_and_template_cache_for_review_and_showcase(self):
        env = dict(os.environ)
        env.pop("HARDREAD_EXPLAIN_MODE", None)
        default = subprocess.check_output([sys.executable, "-c", "import server; print(server.EXPLAIN_MODE)"],
                                          cwd=Path(__file__).parent, env=env, text=True).strip()
        self.assertEqual(default, "auto")
        _, offered = P.build_view_with_offered(REVIEW, CTX)
        keys = S._offered_keys(offered)
        showcase_file = S.SHOWCASES / "showcase-1.explain.json"
        before = showcase_file.read_bytes()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs), \
                 patch.object(S, "EXPLAIN_MODE", "template"), patch.object(S, "glimmer_up", return_value=True), \
                 patch.object(S, "load_parts", return_value=(REVIEW, CTX, {"private": False})), \
                 patch.object(S, "verdicts_of", return_value={}):
                app = S.create_app()
                endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/explain"
                                and "POST" in getattr(r, "methods", set()))
                for rid in ("r" + "a" * 19, "showcase-1"):
                    store = S.explain_store(rid)
                    store.parent.mkdir(parents=True, exist_ok=True)
                    store.write_text(json.dumps({"q3": {"final": "glimmer_raw", "rendered": {"text": "MODEL LEAK"},
                                                         "policy_version": S.EXPLANATION_POLICY_VERSION}}))
                    S._save_explain_keys(rid, "q3", keys, "template")
                    self.assertEqual(S.load_view(rid)["explanations"], {})
                    request = RequestBody({"review_id": rid, "question": "q3", "offered_keys": keys})
                    self.assertTrue(asyncio.run(endpoint(request))["cached"])
                    cached = S.load_view(rid)["explanations"]["q3"]
                    self.assertEqual(cached["final"], "template")
                    self.assertNotIn("MODEL LEAK", json.dumps(cached))
                    with patch.object(S, "_template_explanation", side_effect=AssertionError("not reused")):
                        self.assertTrue(asyncio.run(endpoint(request))["cached"])
                    self.assertIsNone(app.state.jobs.files)
                self.assertEqual(showcase_file.read_bytes(), before)

    def test_legacy_fallback_text_is_withheld_and_product_view_is_sanitized(self):
        _, offered = P.build_view_with_offered(REVIEW, CTX)
        keys = S._offered_keys(offered)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs), \
                 patch.object(S, "EXPLAIN_MODE", "template"), \
                 patch.object(S, "load_parts", return_value=(REVIEW, CTX, {"private": False})), \
                 patch.object(S, "verdicts_of", return_value={}):
                rid = "showcase-1"
                store = S.explain_store(rid)
                store.parent.mkdir(parents=True, exist_ok=True)
                legacy = {"final": "template", "requested_mode": "loop", "policy_rejected": True,
                          "rejected": {"text": "HISTORICAL_MODEL_TEXT"},
                          "policy_version": S.EXPLANATION_POLICY_VERSION}
                store.write_text(json.dumps({"q3": legacy}))
                S._save_explain_keys(rid, "q3", keys, "template")
                app = S.create_app()
                get = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/review/{rid}")
                post = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/explain"
                            and "POST" in getattr(r, "methods", set()))
                self.assertNotIn("HISTORICAL_MODEL_TEXT", json.dumps(get(rid)))
                response = asyncio.run(post(RequestBody({"review_id": rid, "question": "q3", "offered_keys": keys})))
                self.assertTrue(response["cached"])
                self.assertNotIn("HISTORICAL_MODEL_TEXT", json.dumps(get(rid)))
                cache = json.loads(store.read_text())
                cache["q3"]["rejected"] = {"text": "HISTORICAL_MODEL_TEXT"}
                cache["q3"]["model"] = "MODEL_METADATA"
                store.write_text(json.dumps(cache))
                self.assertNotIn("HISTORICAL_MODEL_TEXT", json.dumps(get(rid)))
                self.assertNotIn("MODEL_METADATA", json.dumps(get(rid)))

    def test_loop_and_reachable_auto_do_not_reuse_product_template(self):
        _, offered = P.build_view_with_offered(REVIEW, CTX)
        keys = S._offered_keys(offered)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs), \
                 patch.object(S, "load_parts", return_value=(REVIEW, CTX, {"private": False})), \
                 patch.object(S, "verdicts_of", return_value={}), patch.object(S, "glimmer_up", return_value=True), \
                 patch.object(S, "eval_locked", return_value=False):
                app = S.create_app()
                endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/explain"
                                and "POST" in getattr(r, "methods", set()))
                request = RequestBody({"review_id": "showcase-1", "question": "q3", "offered_keys": keys})
                with patch.object(S, "EXPLAIN_MODE", "template"):
                    self.assertTrue(asyncio.run(endpoint(request))["cached"])
                for mode in ("loop", "auto"):
                    app.state.jobs.files = None
                    with patch.object(S, "EXPLAIN_MODE", mode):
                        self.assertEqual(S.load_view("showcase-1")["explanations"], {})
                        self.assertEqual(asyncio.run(endpoint(request))["job_id"], "synthetic-job")
                        self.assertIsNotNone(app.state.jobs.files)
                        self.assertIn(":loop:", app.state.jobs.key)

    def test_engineering_template_fallback_remains_reusable_in_same_mode(self):
        _, offered = P.build_view_with_offered(REVIEW, CTX)
        keys = S._offered_keys(offered)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs), \
                 patch.object(S, "EXPLAIN_MODE", "loop"), \
                 patch.object(S, "load_parts", return_value=(REVIEW, CTX, {"private": False})), \
                 patch.object(S, "verdicts_of", return_value={}):
                app = S.create_app()
                post = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/explain"
                            and "POST" in getattr(r, "methods", set()))
                request = RequestBody({"review_id": "showcase-1", "question": "q3", "offered_keys": keys})
                self.assertEqual(asyncio.run(post(request))["job_id"], "synthetic-job")
                job = root / "fallback-job"
                job.mkdir()
                (job / "explain.json").write_text(json.dumps({"final": "template", "requested_mode": "loop",
                                                                 "rendered": {"text": "fallback"}}))
                self.assertEqual(app.state.jobs.on_done({"dir": job}), "q3")
                self.assertTrue(asyncio.run(post(request))["cached"])
                self.assertEqual(S.load_view("showcase-1")["explanations"]["q3"]["rendered"]["text"], "fallback")
                with patch.object(S, "EXPLAIN_MODE", "template"):
                    self.assertEqual(S.load_view("showcase-1")["explanations"], {})

    def test_ui_wait_and_provenance_describe_actual_mode(self):
        js = (Path(__file__).parent / "static" / "app.js").read_text()
        initial = js.split("function load(q) {", 1)[1].split('api("/api/explain"', 1)[0]
        self.assertNotIn("spinner", initial)
        self.assertNotIn("waiting(", initial)
        self.assertIn('lv.explain_mode === "template"', js)
        self.assertIn("fixed application code supplies the words", js)
        self.assertNotIn("Muse Glimmer runs here as open weights", js)

    def test_template_failure_does_not_submit_a_model_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs), \
                 patch.object(S, "EXPLAIN_MODE", "template"), \
                 patch.object(S, "load_parts", return_value=(REVIEW, CTX, {"private": False})), \
                 patch.object(S, "verdicts_of", return_value={}), \
                 patch.object(S, "_template_explanation", return_value=None):
                app = S.create_app()
                endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/explain"
                                and "POST" in getattr(r, "methods", set()))
                _, offered = P.build_view_with_offered(REVIEW, CTX)
                response = asyncio.run(endpoint(RequestBody({"review_id": "showcase-1", "question": "q3",
                                                              "offered_keys": S._offered_keys(offered)})))
                self.assertEqual(response.status_code, 503)
                self.assertIsNone(app.state.jobs.files)

    def test_real_review_and_showcase_routes_use_temporary_state(self):
        showcase_file = S.SHOWCASES / "showcase-1.explain.json"
        original = showcase_file.read_bytes()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs), \
                 patch.object(S, "EXPLAIN_MODE", "template"), patch.object(S, "glimmer_up", return_value=True):
                app = S.create_app()
                rid = S.store_review("a" * 64, REVIEW, CTX)
                review_endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/review/{rid}")
                explain_endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/explain"
                                        and "POST" in getattr(r, "methods", set()))
                for review_id in (rid, "showcase-1"):
                    view = review_endpoint(review_id)
                    keys = [{"turn": d["turn"], "k": d["k"]} for d in view["disagreements"]]
                    for qid in ("q1", "q2", "q3"):
                        response = asyncio.run(explain_endpoint(RequestBody({"review_id": review_id,
                                                    "question": qid, "offered_keys": keys})))
                        self.assertTrue(response["cached"])
                    view = review_endpoint(review_id)
                    self.assertEqual([view["explanations"][q]["final"] for q in ("q1", "q2", "q3")],
                                     ["template"] * 3)
                self.assertIsNone(app.state.jobs.files)
                self.assertEqual(showcase_file.read_bytes(), original)

    def test_model_job_key_is_mode_scoped_and_late_model_result_is_withheld(self):
        _, offered = P.build_view_with_offered(REVIEW, CTX)
        keys = S._offered_keys(offered)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs), \
                 patch.object(S, "EXPLAIN_MODE", "loop"), \
                 patch.object(S, "load_parts", return_value=(REVIEW, CTX, {"private": False})), \
                 patch.object(S, "verdicts_of", return_value={}):
                app = S.create_app()
                endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/explain"
                                and "POST" in getattr(r, "methods", set()))
                result = asyncio.run(endpoint(RequestBody({"review_id": "showcase-1", "question": "q3",
                                                           "offered_keys": keys})))
                self.assertEqual(result["job_id"], "synthetic-job")
                self.assertIn(":loop:", app.state.jobs.key)
                job = root / "late"
                job.mkdir()
                (job / "explain.json").write_text(json.dumps({"final": "glimmer_raw", "rendered": {"text": "late"}}))
                with patch.object(S, "EXPLAIN_MODE", "template"):
                    self.assertEqual(app.state.jobs.on_done({"dir": job}), "withheld")
                self.assertFalse(S.explain_store("showcase-1").exists())

    def test_static_showcase_uses_templates_and_exact_offered(self):
        _, offered = P.build_view_with_offered(REVIEW, CTX)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "index.json").write_text(json.dumps({"showcases": [{"id": "showcase-1"}]}))
            (root / "showcase-1.review.json").write_text(json.dumps(REVIEW))
            (root / "showcase-1.ctx.json").write_text(json.dumps(CTX))
            (root / "showcase-1.explain.json").write_text(json.dumps({"q3": {"final": "glimmer_raw",
                                                                          "rendered": {"text": "MODEL LEAK"}}}))
            S._agent("explain")
            model = sys.modules["explain"]
            with patch.object(static_export, "SHOW", root), \
                 patch.object(sys, "argv", ["static_export.py", "--showcases", "--out", str(root / "out.html")]), \
                 patch.object(model, "run", wraps=model.run) as run:
                static_export.main()
            html = (root / "out.html").read_text()
            self.assertNotIn("MODEL LEAK", html)
            self.assertEqual(len(run.call_args_list), 3)
            self.assertTrue(all(call.kwargs["mode"] == "template" and call.kwargs["offered"] == offered
                                for call in run.call_args_list))
            data = json.loads(html.split("window.HARDREAD_STATIC = ", 1)[1].split(";\nif (!location.hash)", 1)[0])
            self.assertEqual([x["final"] for x in data["views"]["showcase-1"]["explanations"].values()],
                             ["template"] * 3)


class ReadContextTests(unittest.TestCase):
    def verdict(self, belief):
        return {"turn": 2, "k": 0, "X": "switch ragingbolt", "chosen": "swordsdance",
                "verdict": "Misplay", "mean_delta_U": 0.08, "mean_delta_R": 0.07,
                "delta_U": [0.08, 0.09, 0.07], "delta_R": [0.07, 0.06, 0.08],
                "margin": 0.08, "ok_margin": 0.02, "seeds": [11, 12, 13], "iterations": 60000,
                "worlds": 4, "reference": "engine", "stated": {"sets": belief.get("sets", {})},
                "app_belief": belief, "cache_identity_sha256": "unchanged-receipt"}

    def render(self, card):
        script = r"""
const fs = require('fs'), vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const helpers = source.slice(source.indexOf('  function h(tag, attrs)'), source.indexOf('  function clear(el)'));
const body = source.slice(source.indexOf('  var VERDICT_GLYPH'), source.indexOf('  // ------------------------------------------------------------------ share'));
function element(tag) { return { tag, attrs: {}, children: [], style: { setProperty() {} },
  setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {}, appendChild(c) { this.children.push(c); },
  set textContent(v) { this.children = [{text: String(v)}]; }, get textContent() { return ''; } }; }
const context = {document: {createElement: element, createElementNS: (_, tag) => element(tag),
  createTextNode: (text) => ({text})}, SVGNS: 'svg', MINUS: '−', STATIC: false};
vm.createContext(context); vm.runInContext(helpers + body, context);
const card = JSON.parse(fs.readFileSync(0, 'utf8'));
const output = context.verdictCard(card, 'r123', 'receipt123');
process.stdout.write(JSON.stringify(output));
"""
        result = subprocess.run(["node", "-e", script, str(Path(__file__).parent / "static" / "app.js")],
                                input=json.dumps(card), text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def test_confidence_only_retains_engine_values_and_renders_neutral_context(self):
        raw = self.verdict({"sets": {}, "confidence": "guess"})
        before = json.loads(json.dumps(raw))
        card = P.verdict_card(raw, CTX, REVIEW)
        self.assertEqual(raw, before)
        self.assertFalse(card["has_stated_hypothesis"])
        self.assertIn("In this engine comparison", card["headline"])
        self.assertEqual(card["verdict"], "Misplay")
        self.assertEqual(card["delta_U"], [8.0, 9.0, 7.0])
        self.assertEqual(card["delta_R"], [7.0, 6.0, 8.0])
        self.assertEqual((card["margin_pts"], card["runs"], card["iterations"], card["worlds"]), (8, 3, 60000, 4))
        rendered = json.dumps(self.render(card))
        self.assertIn("No testable read was recorded", rendered)
        self.assertIn("Engine baseline", rendered)
        self.assertNotIn("under your read", rendered)
        self.assertIn("receipt123", rendered)

    def test_substantive_set_and_action_show_conditional_and_checked(self):
        belief = {"sets": {"Pikachu": {"item": "lightball"}},
                  "expects_action": {"kind": "move", "move": "shadowball"}, "confidence": "think"}
        card = P.verdict_card(self.verdict(belief), CTX, REVIEW)
        self.assertTrue(card["has_stated_hypothesis"])
        self.assertIn("In this engine comparison", card["headline"])
        self.assertEqual(card["read_check"]["status"], "checked")
        rendered = json.dumps(self.render(card))
        self.assertIn("under your read", rendered)
        self.assertIn('"data-read-check-status": "checked"', rendered)
        self.assertNotIn("No testable read was recorded", rendered)

    def test_no_opponent_choice_and_missing_data_never_claim_checked_action(self):
        belief = {"sets": {}, "expects_action": {"kind": "move", "move": "shadowball"}}
        review = json.loads(json.dumps(REVIEW))
        review["decisions"][0]["opponent_policy_top"] = [["No Move", 1.0]]
        card = P.verdict_card(self.verdict(belief), CTX, review)
        self.assertEqual(card["read_check"]["status"], "no_opponent_choice")
        rendered = json.dumps(self.render(card))
        self.assertIn("no real choice", rendered)
        self.assertNotIn('"data-read-check-status": "checked"', rendered)
        self.assertNotIn("Even if your read were right", rendered)
        card = P.verdict_card(self.verdict(belief), CTX, None)
        self.assertIn("expected opponent action could not be checked", json.dumps(self.render(card)))

    def test_action_only_checked_and_no_choice_plot_engine_baseline(self):
        belief = {"sets": {}, "expects_action": {"kind": "move", "move": "shadowball"}}
        no_choice = json.loads(json.dumps(REVIEW))
        no_choice["decisions"][0]["opponent_policy_top"] = [["No Move", 1.0]]
        for review, status in ((REVIEW, "checked"), (no_choice, "no_opponent_choice")):
            card = P.verdict_card(self.verdict(belief), CTX, review)
            self.assertTrue(card["has_stated_hypothesis"])
            self.assertFalse(card["has_retained_set_hypothesis"])
            self.assertEqual(card["read_check"]["status"], status)
            dom = self.render(card)
            text = json.dumps(dom)
            self.assertIn('"text": "Engine baseline"', text)
            self.assertIn("Engine baseline 8, 9, 7", text)
            self.assertNotIn("under your read", text)
            self.assertIn('"data-read-check-status": "' + status + '"', text)

    def test_removed_set_uses_baseline_plot(self):
        belief = {"sets": {"Pikachu": {"item": "lightball"}}}
        verdict = self.verdict(belief)
        verdict["stated"] = {"sets": {}}
        card = P.verdict_card(verdict, CTX, REVIEW)
        self.assertFalse(card["has_retained_set_hypothesis"])
        rendered = json.dumps(self.render(card))
        self.assertIn("Engine baseline", rendered)
        self.assertIn("In this engine comparison", rendered)
        self.assertNotIn("Even if your read were right", rendered)
        verdict["info_third_option"] = {"option": "rapidspin"}
        rendered = json.dumps(self.render(P.verdict_card(verdict, CTX, REVIEW)))
        self.assertIn("In this engine comparison", rendered)
        self.assertNotIn("Under your read", rendered)

    def test_empty_or_invalid_details_do_not_count_as_hypotheses(self):
        for belief in ({"sets": {"Pikachu": {"item": "  ", "moves_has": [""]}}},
                       {"sets": {"": {"item": "lightball"}}},
                       {"expects_action": {"kind": "move", "move": ""}},
                       {"expects_action": {"kind": "invalid"}}):
            self.assertEqual(P._stated_hypotheses(belief), (False, False))
        for belief in ({"expects_action": {"kind": "switch"}},
                       {"expects_action": {"kind": "tera"}},
                       {"sets": {"Pikachu": {"moves_not": ["thunderbolt"]}}}):
            self.assertTrue(any(P._stated_hypotheses(belief)))


def sanitize_fixture(p1, p2, winner):
    return "\n".join([
        f"|player|p1|{p1}|lucas|1500", f"|player|p2|{p2}|dawn|1500", "|c|+someone|hello there", "|gametype|singles",
        "|tier|[Gen 9] OU", "|poke|p1|Landorus-Therian, M|", "|poke|p2|Great Tusk|", "|teampreview", "|",
        "|switch|p1a: Rockruff|Rockruff, M|100/100", "|switch|p2a: Great Tusk|Great Tusk|100/100", "|turn|1", "|",
        "|move|p1a: Rockruff|Stealth Rock|p2a: Great Tusk", f"|-sidestart|p2: {p2}|move: Stealth Rock",
        "|move|p2a: Great Tusk|U-turn|p1a: Rockruff", "|-damage|p1a: Rockruff|50/100 tox|[from] psn",
        "|turn|2", "|", f"|-message|{p1} forfeited.", "|", f"|win|{winner}"])


class ReplayInputTests(unittest.TestCase):
    """Failures found by pushing public replays through the app (one regression test per fix)."""

    def endpoint(self, path):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(S, "CACHE", root / "cache"), patch.object(S, "JOBS", root / "jobs"), \
                 patch.object(S, "STATE", root / "state"), patch.object(S, "Jobs", FakeJobs):
                app = S.create_app()
        return next(r.endpoint for r in app.routes if getattr(r, "path", None) == path)

    def test_username_that_is_game_vocabulary_does_not_corrupt_the_log(self):
        for p1, p2, broken in (("Rock", "Beta Two", "Stealth Player1"), ("turn", "Beta Two", "U-Player1"),
                               ("a", "b", "|plPlayer1yer|")):
            rep = {"log": sanitize_fixture(p1, p2, p1)}
            text = "\n".join(P.sanitized_lines(rep, {"p1": p1, "p2": p2}))
            self.assertNotIn(broken, text)
            self.assertIn("|Stealth Rock|", text)
            self.assertIn("|turn|1", text)
            self.assertIn("|U-turn|", text)
            self.assertIn("|player|p1|Player1||1500", text)
            self.assertIn("|-sidestart|p2: Player2|move: Stealth Rock", text)
            self.assertIn("|-message|Player1 forfeited.", text)
            self.assertIn("|win|Player1", text)
            self.assertNotIn("hello there", text)

    def test_username_inside_another_username_keeps_the_right_winner(self):
        rep = {"log": sanitize_fixture("Bob7by", "Bob7", "Bob7")}
        lines = P.sanitized_lines(rep, {"p1": "Bob7by", "p2": "Bob7"})
        self.assertIn("|win|Player2", lines)
        self.assertIn("|-message|Player1 forfeited.", lines)

    def test_username_in_a_nickname_is_still_removed_and_ordinary_names_are_unchanged(self):
        log = sanitize_fixture("Rockfan", "Beta Two", "Rockfan").replace("Rockruff", "ROCKFAN jr")
        lines = P.sanitized_lines({"log": log}, {"p1": "Rockfan", "p2": "Beta Two"})
        self.assertIn("p1a: Player1 jr", "\n".join(lines))
        self.assertNotIn("ROCKFAN jr|", "\n".join(lines))
        names = {"p1": "Alpha One", "p2": "Beta Two"}
        log = sanitize_fixture(names["p1"], names["p2"], names["p1"])
        self.assertEqual(P.sanitized_lines({"log": log}, names), P.PR.sanitize_log(log, names, P.PSEUDO))

    def test_unreadable_or_non_text_input_gets_a_clear_rejection(self):
        for path, extra in (("/api/preflight", {}), ("/api/review", {"side": "p1"})):
            post = self.endpoint(path)
            for body in ({"input": "|turn|abc\n|win|x"}, {"input": "|||||\n|turn|\n|win|"}, {"input": 123},
                         {"input": ["x"]}, {"input": {"a": 1}},
                         {"input": sanitize_fixture("Alpha One", "Beta Two", "Alpha One"), "team": 123}):
                response = asyncio.run(post(RequestBody(dict(body, **extra))))
                self.assertEqual(response.status_code, 422, body)
                self.assertTrue(json.loads(response.body)["error"])

    def test_unexpected_failure_while_reading_input_is_not_a_raw_500(self):
        for path, extra in (("/api/preflight", {}), ("/api/review", {"side": "p1"})):
            post = self.endpoint(path)
            with patch.object(P, "inspect_game", side_effect=RuntimeError("secret detail")):
                response = asyncio.run(post(RequestBody(dict({"input": "|turn|1\n|win|x"}, **extra))))
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("secret detail", response.body.decode())

    def test_oversized_paste_says_so(self):
        self.assertTrue(S._too_large(str(S.MAX_BODY + 1)))
        self.assertFalse(S._too_large(str(S.MAX_BODY)))
        self.assertFalse(S._too_large(None))
        self.assertFalse(S._too_large("not a number"))
        with patch.object(S, "Jobs", FakeJobs), tempfile.TemporaryDirectory() as td:
            with patch.object(S, "CACHE", Path(td) / "cache"), patch.object(S, "JOBS", Path(td) / "jobs"):
                app = S.create_app()
        from fastapi.testclient import TestClient
        response = TestClient(app).post("/api/preflight", json={"input": "x" * (S.MAX_BODY + 1)})
        self.assertEqual(response.status_code, 413)
        self.assertIn("1 MB", response.json()["error"])

    def test_tie_is_reported_as_a_tie(self):
        review = json.loads(json.dumps(REVIEW))
        del review["bridge"]
        review["summary"]["result"] = None
        review["meta"]["end_reason"] = "tie"
        view = P.build_view(review, dict(CTX, winner="tie", end_reason="tie"))
        self.assertEqual(view["result"], "tie")
        self.assertTrue(view["bridge"]["tie"])
        self.assertFalse(view["bridge"]["available"])
        self.assertIn("tie", view["summary_text"])
        del review["meta"]["end_reason"]
        self.assertFalse(P.bridge_numbers(review)["tie"])

    def test_team_export_of_the_wrong_side_is_rejected_before_the_engine_runs(self):
        team = "\n\n".join(f"{name} @ Leftovers\n- Protect" for name in ("Pikachu", "Eevee", "Mew", "Ditto", "Snorlax"))
        roster = SimpleNamespace(roster=[SimpleNamespace(species_preview=x) for x in
                                         ("Pikachu", "Eevee", "Mew", "Ditto", "Snorlax", "Abra")])
        other = SimpleNamespace(roster=[SimpleNamespace(species_preview=x) for x in
                                        ("Zapdos", "Kyurem", "Lugia", "Ho-Oh", "Entei", "Suicune")])
        parser = SimpleNamespace(sides={"p1": roster, "p2": other}, rows=[])
        game = {"parser": parser, "names": {"p1": "Alpha One", "p2": "Beta Two"}, "end_reason": "ko", "turns": 3,
                "winner": "p1"}
        rep = {"log": sanitize_fixture("Alpha One", "Beta Two", "Alpha One")}
        with patch.object(P, "side_rows", return_value=[]):
            self.assertEqual(P.prepare(rep, "p1", team, game)[1]["mode"], "export")
            with self.assertRaisesRegex(P.InputError, "does not match"):
                P.prepare(rep, "p2", team, game)

    def test_showdown_home_page_is_not_called_a_live_battle(self):
        with self.assertRaisesRegex(P.InputError, "Only replay.pokemonshowdown.com"):
            P.classify_input("https://pokemonshowdown.com/")
        with self.assertRaisesRegex(P.InputError, "live battle"):
            P.classify_input("https://play.pokemonshowdown.com/battle-gen9ou-123456")

    def run_failing_job(self, script):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(S, "JOBS", Path(td)):
                jobs = S.Jobs(1)
                try:
                    jid = jobs.submit("review", "k", {}, lambda d: [sys.executable, "-c", script, str(d)], lambda j: None)
                    for _ in range(100):
                        st = jobs.status(jid)
                        if st["state"] in ("done", "error"):
                            return st
                        time.sleep(0.1)
                    self.fail("job did not finish")
                finally:
                    jobs.shutdown()

    def test_failed_job_shows_the_workers_message(self):
        st = self.run_failing_job("import json,sys,pathlib; pathlib.Path(sys.argv[1],'status.json').write_text("
                                  "json.dumps({'state':'error','message':'The engine could not review this game.'}));"
                                  "sys.exit(1)")
        self.assertEqual(st["state"], "error")
        self.assertEqual(st["message"], "The engine could not review this game.")

    def test_killed_job_does_not_show_a_stale_progress_line_as_its_error(self):
        st = self.run_failing_job("import json,sys,pathlib; pathlib.Path(sys.argv[1],'status.json').write_text("
                                  "json.dumps({'state':'running','message':'Searching decision 3 of 9'}));sys.exit(9)")
        self.assertEqual(st["state"], "error")
        self.assertNotIn("Searching", st["message"])


ENGINE_PROBE = r"""
import json, sys
sys.path[:0] = [sys.argv[1] + "/tools/review"]
import reconstruct as R
from copy import deepcopy
from fp.battle.state import Battle, LastUsedMove, Pokemon
from fp.search.poke_engine_helpers import battle_to_poke_engine_state
from fp.modes import battle_mode
from fp.format_spec import FormatSpec
out = {}
lines, _ = R.normalize_log(["|-weather|Snow", "|-weather|Snow|[upkeep]", "|-weather|Snowscape", "|-weather|none",
                            "|move|p1a: X|Snow"], "p1", {})
out["weather"] = lines
battle = Battle("battle-test")
battle.mode = battle_mode(FormatSpec.from_format_string("gen9ou").battle_type)
out["no_stats"] = battle.mode.smogon_sets.move_usage_rates(Pokemon("Roaring Moon", 100))
for side in (battle.user, battle.opponent):
    side.active = Pokemon("Umbreon", 100)
    for move in ("toxic", "wish", "protect", "substitute", "hypervoice"):
        side.active.add_move(move)
    side.last_used_move = LastUsedMove("umbreon", "hypervoice", 1)
state = battle_to_poke_engine_state(deepcopy(battle))
out["state"] = len(state.to_string()) > 0
print("RESULT " + json.dumps(out))
"""


class EngineGuardTests(unittest.TestCase):
    """Run in the Foul Play environment: real replays crashed the engine on each of these."""

    @classmethod
    def setUpClass(cls):
        if not S.FP_PY.exists() or not P.FP_DIR.exists():
            raise unittest.SkipTest("the Foul Play environment is not available")
        result = subprocess.run([str(S.FP_PY), "-c", ENGINE_PROBE, str(S.HARDREAD)], capture_output=True, text=True,
                                cwd=str(S.HARDREAD), timeout=120)
        line = next((x for x in result.stdout.splitlines() if x.startswith("RESULT ")), None)
        if line is None:
            raise AssertionError("engine probe failed: " + result.stderr[-800:])
        cls.out = json.loads(line[7:])

    def test_old_snow_weather_name_is_translated(self):
        self.assertEqual(self.out["weather"][:2], ["|-weather|Snowscape", "|-weather|Snowscape|[upkeep]"])
        self.assertEqual(self.out["weather"][2:], ["|-weather|Snowscape", "|-weather|none", "|move|p1a: X|Snow"])

    def test_species_without_usage_statistics_has_no_moves_to_sample(self):
        self.assertEqual(self.out["no_stats"], [])

    def test_fifth_known_move_does_not_panic_the_engine(self):
        self.assertTrue(self.out["state"])


if __name__ == "__main__":
    unittest.main()
