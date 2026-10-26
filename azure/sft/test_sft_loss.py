"""CPU tests for the choice-token loss (sft_plugin.py) and the decisive-token rows (build_sft.py).

  python3 azure/sft/test_sft_loss.py        (needs torch, tokenizers, jinja2; uses scratch_ds/sample.jsonl.gz)

A tiny random model with Glimmer's full vocabulary (202,048) and no attention (each position's
logits depend only on its own input token) makes "gradient only where expected" checkable exactly.
"""
from __future__ import annotations

import gzip
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch
import torch.nn.functional as F

HERE = Path(__file__).resolve().parent
HR = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HR / "tools" / "sft"))
import sft_plugin as P  # noqa: E402
import serialize as S  # noqa: E402
from build_sft import choice_token_info  # noqa: E402
from glimmer_chat import GlimmerChat  # noqa: E402

V = 202048
SAMPLE = HR / "scratch_ds" / "sample.jsonl.gz"


class TinyLM(torch.nn.Module):
    def __init__(self, keep_ok=True):
        super().__init__()
        torch.manual_seed(0)
        self.emb = torch.nn.Embedding(V, 8)
        self.head = torch.nn.Linear(8, V)
        self.keep_ok = keep_ok

    def forward(self, input_ids, attention_mask=None, use_cache=None, **kw):
        if "logits_to_keep" in kw and not self.keep_ok:
            raise TypeError("unexpected keyword argument 'logits_to_keep'")
        h = self.emb(input_ids)
        keep = kw.get("logits_to_keep")
        if keep is not None and not isinstance(keep, int):
            h = h[:, keep]
        return SimpleNamespace(logits=self.head(h))


def build_rows(n=4):
    """Action rows exactly as build_sft.py makes them (argmax target), from the committed sample."""
    gc = GlimmerChat()
    games = {g["uid"]: g for g in map(json.loads, gzip.open(SAMPLE.with_name("sample.games.jsonl.gz"), "rt"))}
    rows, picked = [], 0
    for line in gzip.open(SAMPLE, "rt"):
        r = json.loads(line)
        if len(r["legal"]) < 4 or r["team_preview"]:
            continue
        g = games[f"{r['run_id']}/g{r['game']:04d}"]
        ex = S.build_example(r["view"], g["log"][: r["history_end"]], r["legal"], r["player"], r["turn"], r["k"], False)
        pol = r["policy"]
        target = max(pol, key=pol.get)
        enc = gc.encode(ex["messages"], ex["tools"], S.target_message(target, ex["id_map"]))
        inv = {v: k for k, v in ex["id_map"].items()}
        info = choice_token_info(gc, enc, ex["id_map"], inv[target], {inv[a]: p for a, p in pol.items()})
        rows.append(dict(input_ids=enc["input_ids"], labels=enc["labels"], target_text=enc["target_text"],
                         target_id=inv[target], menu=list(ex["id_map"]), **info))
        picked += 1
        if picked == n:
            break
    return gc, rows


@unittest.skipUnless(SAMPLE.exists(), "needs scratch_ds/sample.jsonl.gz")
class ChoiceLossTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gc, cls.rows = build_rows(4)
        # vary lengths so the batch has right padding
        cls.rows[1] = dict(cls.rows[1])
        batch = P.collate(cls.rows[:3], P.PAD_FALLBACK_ID, 2048, None, choice=True)
        cls.batch = {k: torch.tensor(v, dtype=torch.float32 if k == "soft_p" else torch.long) for k, v in batch.items()}

    # --- rows / masks aligned with the rendered tokens
    def test_rows_aligned_with_rendered_tokens(self):
        dec = lambda ids: self.gc.tok.decode(ids, skip_special_tokens=False)  # noqa: E731
        for r in self.rows:
            cp, ids, lab = r["choice_pos"], r["input_ids"], r["labels"]
            self.assertEqual(ids[cp], r["hard_tok"])
            self.assertEqual(lab[cp], ids[cp])
            self.assertEqual(dec(ids[cp - 2: cp + 1]), r["target_id"])          # 'act','_','NN'
            sup = [i for i, x in enumerate(lab) if x != -100]
            self.assertEqual(sup, list(range(sup[0], len(ids))))                # one contiguous span to the end
            self.assertEqual(dec([ids[i] for i in sup]), r["target_text"])     # exactly the rendered target
            self.assertTrue(r["target_text"].startswith(" to=self<|message|>Acting directly.<|eom|><|start|>assistant to=submit_action"))
            self.assertTrue(r["target_text"].endswith("</atem:function_calls><|eot|>"))
            self.assertEqual(dec(ids[sup[0] - 2: sup[0]]), "<|start|>assistant")   # generation prompt unsupervised
            self.assertAlmostEqual(sum(r["soft_p"]), 1.0, places=4)
            self.assertIn(r["hard_tok"], r["soft_tok"])
            self.assertEqual(sorted("act_" + self.gc.tok.id_to_token(t) for t in r["soft_tok"]), sorted(r["menu"]))

    def test_choice_position_is_supervised(self):
        pos = P.supervised_positions(self.batch["labels"])
        for b, cp in enumerate(self.batch["choice_pos"].tolist()):
            self.assertIn(cp - 1, pos.tolist())
            self.assertEqual(int(self.batch["labels"][b, cp]), int(self.batch["input_ids"][b, cp]))

    def _full_logits(self, model):
        return model(self.batch["input_ids"]).logits

    # --- loss values
    def test_plain_equivalence(self):
        """W=1, alpha=0 reproduces the standard shifted token-mean CE (HF / plain mode)."""
        m = TinyLM()
        b = self.batch
        loss, _ = P.choice_loss_step(m, b, w_choice=1.0, alpha=0.0)
        logits = self._full_logits(m)
        ref = F.cross_entropy(logits[:, :-1].reshape(-1, V).float(), b["labels"][:, 1:].reshape(-1), ignore_index=-100)
        self.assertAlmostEqual(float(loss), float(ref), places=5)

    def test_weighted_soft_value(self):
        m = TinyLM()
        b = self.batch
        W, a = 10.0, 0.7
        loss, stats = P.choice_loss_step(m, b, w_choice=W, alpha=a)
        logp = torch.log_softmax(self._full_logits(m).float(), -1)
        num = den = 0.0
        for i in range(b["labels"].shape[0]):
            cp = int(b["choice_pos"][i])
            for t in range(b["labels"].shape[1] - 1):
                y = int(b["labels"][i, t + 1])
                if y == -100:
                    continue
                ce = -float(logp[i, t, y])
                if t + 1 == cp:
                    soft = -sum(float(p) * float(logp[i, t, int(k)]) for k, p in zip(b["soft_tok"][i], b["soft_p"][i]))
                    num += W * (a * soft + (1 - a) * ce)
                    den += W
                else:
                    num += ce
                    den += 1
        self.assertAlmostEqual(float(loss), num / den, places=4)
        self.assertEqual(stats["choice_n"], 3)

    # --- gradients only where expected
    def test_gradients_only_at_supervised_positions(self):
        m = TinyLM()
        b = self.batch
        W, a = 10.0, 0.7
        logits = self._full_logits(m).detach().requires_grad_(True)
        pos = P.supervised_positions(b["labels"])
        loss, _ = P.weighted_choice_loss(logits[:, pos], pos, b["labels"], b["choice_pos"], b["soft_tok"], b["soft_p"], W, a)
        loss.backward()
        g = logits.grad
        nz = g.abs().sum(-1) > 0                                     # [B, T]
        expect = torch.zeros_like(nz)
        expect[:, :-1] = b["labels"][:, 1:] != -100                  # position t predicts token t+1
        self.assertTrue(torch.equal(nz, expect), "gradient outside the supervised positions (prompt/padding)")
        # exact gradient at the choice position: (W / sum w) * (softmax - (a q + (1-a) onehot))
        sumw = float(expect.sum()) - 3 + 3 * W
        for i in range(3):
            t = int(b["choice_pos"][i]) - 1
            p = torch.softmax(logits[i, t].detach().float(), -1)
            tgt = torch.zeros(V)
            tgt.index_add_(0, b["soft_tok"][i], a * b["soft_p"][i])
            tgt[int(b["labels"][i, t + 1])] += 1 - a
            self.assertTrue(torch.allclose(g[i, t], (W / sumw) * (p - tgt), atol=1e-6))
            # and a format token keeps weight 1
            t2 = int(b["choice_pos"][i]) + 1
            p2 = torch.softmax(logits[i, t2].detach().float(), -1)
            oh = torch.zeros(V)
            oh[int(b["labels"][i, t2 + 1])] = 1
            self.assertTrue(torch.allclose(g[i, t2], (1 / sumw) * (p2 - oh), atol=1e-6))

    def test_parameter_gradients_only_from_supervised_inputs(self):
        """No attention in TinyLM: embedding rows of prompt/padding-only tokens must get no gradient."""
        m = TinyLM()
        b = self.batch
        loss, _ = P.choice_loss_step(m, b, w_choice=10.0, alpha=0.7)
        loss.backward()
        rows_with_grad = set((m.emb.weight.grad.abs().sum(-1) > 0).nonzero().squeeze(-1).tolist())
        used = set()
        for i in range(3):
            for t in range(b["labels"].shape[1] - 1):
                if int(b["labels"][i, t + 1]) != -100:
                    used.add(int(b["input_ids"][i, t]))
        self.assertEqual(rows_with_grad, used)

    def test_logits_to_keep_fallback_same_loss(self):
        P._KEEP_OK = None
        l1, _ = P.choice_loss_step(TinyLM(keep_ok=True), self.batch, 10.0, 0.7)
        P._KEEP_OK = None
        l2, _ = P.choice_loss_step(TinyLM(keep_ok=False), self.batch, 10.0, 0.7)
        P._KEEP_OK = None
        self.assertAlmostEqual(float(l1), float(l2), places=5)

    def test_rows_without_choice_are_plain(self):
        """Reads rows (choice_pos = -1) and padded soft slots contribute plain CE only."""
        rows = [dict(r, choice_pos=-1, soft_tok=[], soft_p=[]) for r in self.rows[:2]]
        out = P.collate(rows, P.PAD_FALLBACK_ID, 2048, None, choice=True)
        b = {k: torch.tensor(v, dtype=torch.float32 if k == "soft_p" else torch.long) for k, v in out.items()}
        m = TinyLM()
        loss, stats = P.choice_loss_step(m, b, 10.0, 0.7)
        logits = m(b["input_ids"]).logits
        ref = F.cross_entropy(logits[:, :-1].reshape(-1, V).float(), b["labels"][:, 1:].reshape(-1), ignore_index=-100)
        self.assertAlmostEqual(float(loss), float(ref), places=5)
        self.assertEqual(stats["choice_n"], 0)

    def test_soft_target_minimised_by_policy(self):
        """With alpha=1 the choice term is minimised when the menu distribution equals q (sanity)."""
        r = self.rows[0]
        q = torch.tensor(r["soft_p"])
        toks = torch.tensor(r["soft_tok"])
        def soft_ce(logits):  # noqa: E306
            return -(q * torch.log_softmax(logits, -1)[toks]).sum()
        good = torch.full((V,), -30.0)
        good[toks] = torch.log(q.clamp_min(1e-12))
        bad = torch.full((V,), -30.0)
        bad[toks] = 0.0
        self.assertLess(float(soft_ce(good)), float(soft_ce(bad)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
