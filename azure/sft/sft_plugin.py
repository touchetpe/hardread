"""Axolotl 0.19 plugin: feed pre-tokenized HardRead SFT rows and log step timing.

Same mechanism as the qualified h100 canary (h100/scripts/train_canary.py AuditPlugin):
`load_datasets` returns a TrainDatasetMeta built from our own JSONL (input_ids/labels made by
tools/sft/build_sft.py with the pinned tokenizer + native chat template), and
`get_collator_cls_and_kwargs` supplies a right-padding collator, so Axolotl's chat_template
tokenization and the multimodal collator are bypassed entirely.

Env: HRSFT_STEPS_LOG (optional) path of a JSONL file receiving one line per optimizer step
(wall time, tokens, supervised tokens, loss, lr) for throughput sizing.
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


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    p = str(path)
    op = gzip.open if p.endswith(".gz") else open
    rows = []
    with op(p, "rt") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if len(r["input_ids"]) != len(r["labels"]) or all(x == -100 for x in r["labels"]):
                    raise ValueError(f"bad row {r.get('example_id')}")
                rows.append({"example_id": r["example_id"], "input_ids": r["input_ids"], "labels": r["labels"]})
    if not rows:
        raise ValueError(f"no rows in {p}")
    return rows


class _Counters:
    tokens = 0
    supervised = 0
    rows = 0


COUNTERS = _Counters()


def collate(features, pad_id: int, max_len: int | None = None, multiple: int | None = None):
    n = max(len(f["input_ids"]) for f in features)
    if max_len and n > max_len:
        raise ValueError(f"row longer than sequence_len ({n} > {max_len}); rebuild data with --max-tokens")
    if multiple:
        n = int(math.ceil(n / multiple) * multiple)
    ids, labels, attn = [], [], []
    for f in features:
        k = len(f["input_ids"])
        ids.append(list(f["input_ids"]) + [pad_id] * (n - k))
        labels.append(list(f["labels"]) + [-100] * (n - k))
        attn.append([1] * k + [0] * (n - k))
        COUNTERS.tokens += k
        COUNTERS.supervised += sum(1 for x in f["labels"] if x != -100)
        COUNTERS.rows += 1
    return ids, labels, attn


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
        print(f"[hrsft] train rows={len(rows)} world={world} rows/step={per_step} total_steps={steps}", flush=True)
        return TrainDatasetMeta(train_dataset=Dataset.from_list(rows), eval_dataset=None, total_num_steps=steps)

    def get_collator_cls_and_kwargs(self, cfg: Any, is_eval: bool = False):
        seq_len = int(cfg.get("sequence_len") or 0) or None

        class PreTokenizedCollator:
            def __init__(self, tokenizer: Any, **kwargs: Any) -> None:
                pid = getattr(tokenizer, "pad_token_id", None)
                self.pad_id = int(pid) if pid is not None else PAD_FALLBACK_ID
                self.multiple = kwargs.get("pad_to_multiple_of")

            def __call__(self, features):
                import torch

                ids, labels, attn = collate(features, self.pad_id, seq_len, self.multiple)
                return {"input_ids": torch.tensor(ids, dtype=torch.long),
                        "labels": torch.tensor(labels, dtype=torch.long),
                        "attention_mask": torch.tensor(attn, dtype=torch.long)}

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
                self.last = now
                with open(log_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")

        return [StepTimer()]
