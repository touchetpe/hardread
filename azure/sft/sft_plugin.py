"""Axolotl 0.19 plugin: pre-tokenized HardRead SFT rows, choice-token loss, step timing.

Data path (same mechanism as the qualified h100 canary, h100/scripts/train_canary.py AuditPlugin):
`load_datasets` returns a TrainDatasetMeta built from tools/sft/build_sft.py rows (pinned tokenizer +
native chat template), and `get_collator_cls_and_kwargs` supplies a right-padding collator, so
Axolotl's chat_template tokenization and multimodal collator are bypassed.

Loss (env HRSFT_LOSS):
  plain     Axolotl's default loss (token-mean CE over the target; Cut Cross Entropy plugin).
  weighted  HardReadTrainer.compute_loss (CCE must be off; run_sft.py removes it):
              per supervised token t: l_t = CE(label_t)                      weight 1   (format tokens)
              at the decisive choice-id token c of action rows:
                l_c = alpha * H(q, p) + (1 - alpha) * CE(hard)               weight W
                where q = search policy over the menu's decisive tokens (row soft_tok/soft_p) and
                H(q, p) = -sum_k q_k log p(tok_k) with p the FULL-vocabulary softmax (so mass that
                leaves the menu is still penalised);
              loss = sum_t w_t l_t / sum_t w_t over the micro-batch (a weighted token mean: with W=1,
              alpha=0 it is exactly the plain token-mean CE, and the total weight is normalised so the
              effective learning rate stays comparable; with ~57 target tokens and W=10 the choice
              token carries 10/66 of the loss instead of 1/57).
            Logits are computed only at supervised positions (HF `logits_to_keep` index tensor; falls
            back to full logits if the model does not support it).
Env: HRSFT_CHOICE_W (default 10), HRSFT_SOFT_ALPHA (default 0.7), HRSFT_STEPS_LOG (per-step JSONL).
"""
from __future__ import annotations

import gzip
import json
import math
import os
import time
from pathlib import Path
from typing import Any

try:
    from axolotl.integrations.base import BasePlugin
except ImportError:  # local CPU tests
    class BasePlugin:  # type: ignore[no-redef]
        def __init__(self) -> None:
            pass

PAD_FALLBACK_ID = 200018  # <|finetune_right_pad|>, generation_config pad_token_id
CHOICE_FIELDS = ("choice_pos", "soft_tok", "soft_p")


def loss_mode() -> str:
    return os.environ.get("HRSFT_LOSS", "plain")


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    p = str(path)
    op = gzip.open if p.endswith(".gz") else open
    rows = []
    with op(p, "rt") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if len(r["input_ids"]) != len(r["labels"]) or all(x == -100 for x in r["labels"]):
                raise ValueError(f"bad row {r.get('example_id')}")
            cp = int(r.get("choice_pos", -1))
            if cp >= 0 and (r["labels"][cp] != r["input_ids"][cp] or r["input_ids"][cp] not in r["soft_tok"]):
                raise ValueError(f"choice token misaligned in {r.get('example_id')}")
            rows.append({"example_id": r["example_id"], "input_ids": r["input_ids"], "labels": r["labels"],
                         "choice_pos": cp, "soft_tok": r.get("soft_tok") or [], "soft_p": r.get("soft_p") or []})
    if not rows:
        raise ValueError(f"no rows in {p}")
    return rows


class _Counters:
    tokens = 0
    supervised = 0
    rows = 0
    loss_stats: dict = {}


COUNTERS = _Counters()


def collate(features, pad_id: int, max_len: int | None = None, multiple: int | None = None, choice: bool = False):
    n = max(len(f["input_ids"]) for f in features)
    if max_len and n > max_len:
        raise ValueError(f"row longer than sequence_len ({n} > {max_len}); rebuild data with --max-tokens")
    if multiple:
        n = int(math.ceil(n / multiple) * multiple)
    out = {"input_ids": [], "labels": [], "attention_mask": []}
    for f in features:
        k = len(f["input_ids"])
        out["input_ids"].append(list(f["input_ids"]) + [pad_id] * (n - k))
        out["labels"].append(list(f["labels"]) + [-100] * (n - k))
        out["attention_mask"].append([1] * k + [0] * (n - k))
        COUNTERS.tokens += k
        COUNTERS.supervised += sum(1 for x in f["labels"] if x != -100)
        COUNTERS.rows += 1
    if choice:
        kmax = max(1, max(len(f.get("soft_tok") or []) for f in features))
        out["choice_pos"] = [int(f.get("choice_pos", -1)) for f in features]
        # padded soft slots: token 0 with probability 0 (contributes nothing to the soft term)
        out["soft_tok"] = [list(f.get("soft_tok") or []) + [0] * (kmax - len(f.get("soft_tok") or [])) for f in features]
        out["soft_p"] = [list(f.get("soft_p") or []) + [0.0] * (kmax - len(f.get("soft_p") or [])) for f in features]
    return out


# ----------------------------------------------------------------------------- loss (pure, CPU-testable)

def supervised_positions(labels):
    """Sorted sequence positions t whose logits predict a supervised token labels[:, t+1] (batch union)."""
    valid = labels[:, 1:] != -100
    return valid.any(0).nonzero().squeeze(-1)


def weighted_choice_loss(logits_sel, pos, labels, choice_pos, soft_tok, soft_p, w_choice: float, alpha: float):
    """logits_sel [B, K, V]: logits at sequence positions `pos` [K]. Returns (loss, stats)."""
    import torch

    tgt = labels[:, pos + 1]                                    # [B, K] token predicted at each kept position
    valid = tgt != -100
    logp = torch.log_softmax(logits_sel.float(), dim=-1)
    ce = -logp.gather(-1, tgt.clamp_min(0).unsqueeze(-1)).squeeze(-1) * valid
    w = valid.float()
    term = ce
    stats = {"format_ce_sum": 0.0, "format_n": 0, "choice_n": 0, "choice_hard_sum": 0.0, "choice_soft_sum": 0.0,
             "choice_acc_sum": 0.0}
    rows = (choice_pos >= 0).nonzero().squeeze(-1)
    if rows.numel():
        k = torch.searchsorted(pos, choice_pos[rows] - 1)
        if not torch.equal(pos[k.clamp_max(pos.numel() - 1)], choice_pos[rows] - 1):
            raise ValueError("choice position is not a supervised position")
        lp_c = logp[rows, k]                                     # [R, V]
        soft = -(soft_p[rows].float() * lp_c.gather(-1, soft_tok[rows])).sum(-1)
        hard = ce[rows, k]
        mix = alpha * soft + (1.0 - alpha) * hard
        mask = torch.zeros_like(valid)
        mask[rows, k] = True
        mix_full = torch.zeros_like(ce).index_put((rows, k), mix)
        term = torch.where(mask, mix_full, ce)
        w = torch.where(mask, torch.full_like(w, float(w_choice)), w)
        with torch.no_grad():
            menu_lp = lp_c.gather(-1, soft_tok[rows]).masked_fill(soft_p[rows] <= 0, float("-inf"))
            pred = soft_tok[rows].gather(-1, menu_lp.argmax(-1, keepdim=True)).squeeze(-1)
            stats.update(choice_n=int(rows.numel()), choice_hard_sum=float(hard.sum()), choice_soft_sum=float(soft.sum()),
                         choice_acc_sum=float((pred == tgt[rows, k]).float().sum()))
        fmt = valid & ~mask
    else:
        fmt = valid
    with torch.no_grad():
        stats.update(format_ce_sum=float((ce * fmt).sum()), format_n=int(fmt.sum()))
    loss = (w * term).sum() / w.sum().clamp_min(1e-9)
    return loss, stats


_KEEP_OK: bool | None = None


def selected_logits(model, input_ids, attention_mask, pos):
    """Logits at sequence positions `pos` only (HF logits_to_keep index tensor), full-logit fallback."""
    global _KEEP_OK
    if _KEEP_OK is not False:
        try:
            out = model(input_ids=input_ids, attention_mask=attention_mask, logits_to_keep=pos, use_cache=False)
            if out.logits.shape[1] == pos.numel():
                _KEEP_OK = True
                return out.logits
            print(f"[hrsft] logits_to_keep tensor not honoured (got {tuple(out.logits.shape)}); full logits", flush=True)
        except (TypeError, ValueError, IndexError, RuntimeError) as e:
            print(f"[hrsft] logits_to_keep tensor unsupported ({type(e).__name__}: {e}); full logits", flush=True)
        _KEEP_OK = False
    out = model(input_ids=input_ids, attention_mask=attention_mask, use_cache=False)
    return out.logits[:, pos]


def choice_loss_step(model, inputs: dict, w_choice: float, alpha: float):
    inputs = dict(inputs)
    labels = inputs.pop("labels")
    cp, st, sp = (inputs.pop(k) for k in CHOICE_FIELDS)
    pos = supervised_positions(labels)
    logits = selected_logits(model, inputs["input_ids"], inputs.get("attention_mask"), pos)
    return weighted_choice_loss(logits, pos, labels, cp, st, sp, w_choice, alpha)


def _accumulate(stats: dict):
    for k, v in stats.items():
        COUNTERS.loss_stats[k] = COUNTERS.loss_stats.get(k, 0) + v


def make_trainer_cls():
    from axolotl.core.trainers import AxolotlTrainer

    w_choice = float(os.environ.get("HRSFT_CHOICE_W", "10"))
    alpha = float(os.environ.get("HRSFT_SOFT_ALPHA", "0.7"))

    class HardReadTrainer(AxolotlTrainer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            # compute_loss returns a per-micro-batch mean; let the Trainer divide by the GA steps.
            self.model_accepts_loss_kwargs = False

        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None, **kw):
            loss, stats = choice_loss_step(model, inputs, w_choice, alpha)
            _accumulate(stats)
            return (loss, None) if return_outputs else loss

    print(f"[hrsft] weighted choice loss: W={w_choice} alpha={alpha}", flush=True)
    return HardReadTrainer


class HardReadSFTPlugin(BasePlugin):
    def load_datasets(self, cfg: Any, preprocess: bool = False):
        if preprocess:
            return None
        from datasets import Dataset
        from axolotl.common.datasets import TrainDatasetMeta

        ds_cfg = (cfg.get("datasets") if hasattr(cfg, "get") else cfg.datasets)[0]
        path = ds_cfg.get("path") if hasattr(ds_cfg, "get") else ds_cfg.path
        rows = read_rows(path)
        world = int(os.environ.get("WORLD_SIZE", "1"))
        per_step = int(cfg.micro_batch_size) * int(cfg.gradient_accumulation_steps) * world
        steps = int(cfg.max_steps) if cfg.get("max_steps") else math.ceil(len(rows) / per_step) * int(cfg.num_epochs or 1)
        print(f"[hrsft] train rows={len(rows)} world={world} rows/step={per_step} total_steps={steps} "
              f"loss={loss_mode()} choice_rows={sum(r['choice_pos'] >= 0 for r in rows)}", flush=True)
        return TrainDatasetMeta(train_dataset=Dataset.from_list(rows), eval_dataset=None, total_num_steps=steps)

    def get_trainer_cls(self, cfg: Any):
        return make_trainer_cls() if loss_mode() == "weighted" else None

    def get_collator_cls_and_kwargs(self, cfg: Any, is_eval: bool = False):
        seq_len = int(cfg.get("sequence_len") or 0) or None
        choice = loss_mode() == "weighted"

        class PreTokenizedCollator:
            def __init__(self, tokenizer: Any, **kwargs: Any) -> None:
                pid = getattr(tokenizer, "pad_token_id", None)
                self.pad_id = int(pid) if pid is not None else PAD_FALLBACK_ID
                self.multiple = kwargs.get("pad_to_multiple_of")

            def __call__(self, features):
                import torch

                out = collate(features, self.pad_id, seq_len, self.multiple, choice=choice)
                return {k: torch.tensor(v, dtype=torch.float32 if k == "soft_p" else torch.long) for k, v in out.items()}

        return PreTokenizedCollator, {}

    def add_callbacks_post_trainer(self, cfg: Any, trainer: Any):
        from transformers import TrainerCallback

        log_path = os.environ.get("HRSFT_STEPS_LOG")
        if not log_path or int(os.environ.get("RANK", "0")) != 0:
            return []

        class StepTimer(TrainerCallback):
            def __init__(self):
                self.t0 = None
                self.last = None

            def on_train_begin(self, args, state, control, **kw):
                self.t0 = self.last = time.time()

            def on_log(self, args, state, control, logs=None, **kw):
                if logs:
                    with open(log_path, "a") as f:
                        f.write(json.dumps({"kind": "log", "step": state.global_step, **{
                            k: v for k, v in logs.items() if isinstance(v, (int, float))}}) + "\n")

            def on_step_end(self, args, state, control, **kw):
                now = time.time()
                rec = {"kind": "step", "step": state.global_step, "t": round(now - self.t0, 3), "dt": round(now - self.last, 3),
                       "tokens_cum": COUNTERS.tokens, "supervised_cum": COUNTERS.supervised, "rows_cum": COUNTERS.rows}
                s = COUNTERS.loss_stats
                if s.get("choice_n"):
                    rec.update(choice_hard_ce=round(s["choice_hard_sum"] / s["choice_n"], 4),
                               choice_soft_ce=round(s["choice_soft_sum"] / s["choice_n"], 4),
                               choice_acc=round(s["choice_acc_sum"] / s["choice_n"], 4))
                if s.get("format_n"):
                    rec["format_ce"] = round(s["format_ce_sum"] / s["format_n"], 5)
                COUNTERS.loss_stats = {}
                self.last = now
                with open(log_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")

        return [StepTimer()]
