#!/usr/bin/env python3
"""HardRead Glimmer SFT job (one H100 node): train -> HF eval -> LoRA->GGUF -> llama.cpp serving eval.

  python azure/sft/run_sft.py --snapshot ${{inputs.model_snapshot}} --data data --out ${{outputs.artifacts}} \
      [--max-steps 160] [--micro-batch 4] [--grad-accum 4] [--lr 2e-4] [--save-steps 40] [--epochs 1] \
      [--eval-n 200] [--gen-n 24] [--serve-n 200] [--base-n 30] [--skip-serve]
      [--job-timeout S] [--post-budget S] [--train-timeout S] [--resume-from CKPT] [--eval-only --adapter DIR]

Time budget: the training subprocess gets --train-timeout, by default derived from the AML job timeout
(--job-timeout, passed by submit_sft.sh) minus the time already used, the eval/convert/serve budget
(--post-budget, default 2400 s; measured 1,421 s for eval-n/serve-n 400) and a 600 s margin. Training
stops GRACEFULLY 15 min before that (sft_plugin DeadlineStop: checkpoint + final adapter save; the stop is
all-reduced so every DDP rank stops at the same step); the subprocess timeout is only a backstop.
DDP: every rank trains to a fresh local directory. Rank 0 verifies persistent output before evaluation;
other successful ranks await its explicit success/failure result and fail on deadline. Abrupt termination
before persistence can lose checkpoints. DDP resume is not supported in this fallback.
--resume-from CKPT: resume an interrupted run (adapter, optimizer, scheduler, RNG, data position) with the
same data, world size and hyperparameters. --eval-only --adapter DIR: no training; eval + GGUF + serve.

Stages (each timed, logged under --out, failures recorded and later optional stages still attempted):
  0 preflight: GPU, disk, pinned tokenizer/template hashes in the snapshot == the data manifest's
  1 background: build llama.cpp f8def7fe (CUDA sm_90) in /tmp, download the pinned Q4_K_M GGUF
    to /dev/shm (sha256 checked); /dev/shm is noexec, so binaries stay in /tmp
  2 axolotl train (QLoRA, sft_plugin feeds pre-tokenized rows); DDP checkpoints are local until verified persistence
  3 eval_sft.py teacher (fresh-process reload of the saved adapter)
  4 convert_lora_to_gguf.py (llama.cpp, + one-line LoraTorchTensor.ndim shim needed by the Muse
    converter's Q/K rope un-permute) -> adapter.gguf
  5 llama-server base GGUF + --lora adapter.gguf: eval_sft.py serve (chat+tools and raw no-grammar),
    then the base GGUF alone on --base-n rows for reference
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]  # code root (contains azure/ and tools/)
SFT = CODE / "azure" / "sft"
TOOLS = CODE / "tools" / "sft"
LLAMA_SHA = "f8def7fe168bab245fbf15d3f18b26dbb1ef73c8"
GGUF_URL = ("https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/resolve/"
            "b44f1072cf75e918a1b82a3a17b45c4389b076e7/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf")
GGUF_SHA = "4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e"
PORT = 18150
SERVER_ARGS = ["--alias", "muse-glimmer", "--host", "127.0.0.1", "--port", str(PORT), "--gpu-layers", "99",
               "--temp", "0", "--top-k", "40", "--top-p", "1", "--seed", "42", "--ctx-size", "8192",
               "--parallel", "1", "--jinja", "--reasoning-format", "deepseek",
               "--chat-template-kwargs", '{"reasoning_strength":"low"}', "--no-webui", "--swa-full"]

OUT: Path
STATE: dict = {"stages": {}}
RANK = int(os.environ.get("RANK", "0"))  # set by AML `distribution: pytorch` on multi-node jobs
SUFFIX = "" if RANK == 0 else f"-rank{RANK}"
LOCAL_OUTPUT: Path | None = None
MIN_LOCAL_FREE = 40 * 1024**3  # conservative floor, not a guarantee against disk exhaustion


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(OUT / f"run{SUFFIX}.log", "a") as f:
        f.write(line + "\n")


def save_state():
    (OUT / f"summary{SUFFIX}.json").write_text(json.dumps(STATE, indent=1))


def stage(name):
    def deco(fn):
        def run(*a, **kw):
            t = time.time()
            log(f"== stage {name}")
            STATE["stages"][name] = {"status": "running"}
            save_state()
            try:
                res = fn(*a, **kw)
                STATE["stages"][name] = {"status": "ok", "seconds": round(time.time() - t, 1), **(res or {})}
                return res
            except Exception as e:  # noqa: BLE001
                STATE["stages"][name] = {"status": "failed", "seconds": round(time.time() - t, 1),
                                         "error": f"{type(e).__name__}: {str(e)[:800]}"}
                log(f"stage {name} FAILED: {e!r}")
                return None
            finally:
                save_state()
        return run
    return deco


def sh(cmd, log_name, env=None, cwd=None, timeout=None):
    log("$ " + " ".join(map(str, cmd))[:400])
    with open(OUT / log_name, "a") as lf:
        p = subprocess.run(list(map(str, cmd)), stdout=lf, stderr=subprocess.STDOUT, env=env, cwd=cwd, timeout=timeout)
    if p.returncode != 0:
        tail = (OUT / log_name).read_text(errors="replace")[-1500:]
        raise RuntimeError(f"{cmd[0]} exited {p.returncode}; tail: {tail}")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


# ----------------------------------------------------------------------------- stages

@stage("preflight")
def preflight(args):
    sys.path.insert(0, str(TOOLS))
    from glimmer_chat import GlimmerChat
    gc = GlimmerChat(args.snapshot)  # raises unless tokenizer/template match the pinned revision
    man = json.loads((Path(args.data) / "manifest.json").read_text())
    if man["tokenizer_sha256"] != gc.tokenizer_sha256 or man["chat_template_sha256"] != gc.template_sha256:
        raise RuntimeError("data was tokenized with a different tokenizer/template than the snapshot's")
    try:
        smi = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True).stdout.strip()
    except OSError as e:
        smi = f"unavailable: {e}"
    du = {p: shutil.disk_usage(p)._asdict() for p in ("/tmp", "/dev/shm") if os.path.exists(p)}
    vers = {}
    for m in ("torch", "transformers", "peft", "axolotl", "bitsandbytes", "datasets"):
        try:
            vers[m] = __import__(m).__version__
        except Exception as e:  # noqa: BLE001
            vers[m] = f"unavailable: {e}"
    return {"gpu": smi, "disk": du, "versions": vers, "data_counts": man["counts"],
            "serializer_version": man["serializer_version"]}


def prefetch(work: Path, shm: Path, res: dict):
    """Background: build llama.cpp and fetch the base GGUF while the GPU trains."""
    try:
        t = time.time()
        src = work / "llama.cpp"
        if not (src / "build" / "bin" / "llama-server").exists():
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "cmake==3.31.6", "ninja"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            tgz = work / "llama.tgz"
            urllib.request.urlretrieve(f"https://codeload.github.com/ggml-org/llama.cpp/tar.gz/{LLAMA_SHA}", tgz)
            src.mkdir(parents=True, exist_ok=True)
            subprocess.run(["tar", "xzf", str(tgz), "-C", str(src), "--strip-components=1"], check=True)
            bindir = Path(sys.executable).parent
            cmake = shutil.which("cmake") or str(bindir / "cmake")
            ninja = shutil.which("ninja") or str(bindir / "ninja")
            with open(OUT / "llama_build.log", "w") as lf:
                subprocess.run([cmake, "-S", src, "-B", src / "build", "-G", "Ninja", "-DGGML_CUDA=ON",
                                "-DCMAKE_CUDA_ARCHITECTURES=90", "-DLLAMA_CURL=OFF", "-DCMAKE_BUILD_TYPE=Release",
                                f"-DCMAKE_MAKE_PROGRAM={ninja}"], check=True, stdout=lf, stderr=subprocess.STDOUT)
                subprocess.run([cmake, "--build", src / "build", "--target", "llama-server", "-j", "24"], check=True,
                               stdout=lf, stderr=subprocess.STDOUT)
        res["llama_build_s"] = round(time.time() - t, 1)
        res["llama_src"] = str(src)
        t = time.time()
        gguf = shm / "base-Q4_K_M.gguf"
        if not gguf.exists():
            urllib.request.urlretrieve(GGUF_URL, gguf)
        if sha256(gguf) != GGUF_SHA:
            raise RuntimeError("base GGUF sha256 mismatch")
        res["gguf"] = str(gguf)
        res["gguf_s"] = round(time.time() - t, 1)
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
    finally:
        res["done"] = True


def _train(args, env):
    import yaml
    cfg = yaml.safe_load((SFT / "qlora_sft.yaml").read_text())
    cfg["base_model"] = str(Path(args.snapshot).resolve())
    cfg["datasets"][0]["path"] = str((Path(args.data) / "train.jsonl.gz").resolve())
    cfg["output_dir"] = str(LOCAL_OUTPUT if is_ddp() else OUT / "checkpoints")
    if is_ddp():
        if cfg.get("save_total_limit") != 3:
            raise ValueError("DDP persistence requires save_total_limit=3")
        cfg["auto_resume_from_checkpoints"] = False
    for key, val in (("max_steps", args.max_steps), ("micro_batch_size", args.micro_batch),
                     ("gradient_accumulation_steps", args.grad_accum), ("learning_rate", args.lr),
                     ("save_steps", args.save_steps), ("num_epochs", args.epochs), ("lora_r", args.lora_r),
                     ("lora_alpha", args.lora_alpha), ("warmup_steps", args.warmup_steps)):
        if val is not None:
            cfg[key] = val
    if not args.max_steps:
        cfg.pop("max_steps", None)
    if args.resume_from:
        ck = Path(args.resume_from)
        missing = [f for f in ("trainer_state.json", "optimizer.pt", "scheduler.pt", "adapter_model.safetensors")
                   if not (ck / f).exists()]
        if missing:
            raise FileNotFoundError(f"resume checkpoint {ck} lacks {missing}")
        n_rng = len(list(ck.glob("rng_state*.pth")))
        world = int(os.environ.get("WORLD_SIZE", "1"))
        if n_rng and n_rng != world:
            log(f"WARNING: checkpoint has {n_rng} rng states but WORLD_SIZE={world}; data skipping assumes the same world")
        cfg["resume_from_checkpoint"] = str(ck)
    if int(os.environ.get("WORLD_SIZE", "1")) > 1:
        # Only LoRA params train and all are used every step; HF would default to True for a PEFT model.
        cfg["ddp_find_unused_parameters"] = False
    if args.loss == "weighted":
        # The choice-token loss needs logits at the supervised positions: Cut Cross Entropy off.
        cfg["plugins"] = [p for p in cfg["plugins"] if "cut_cross_entropy" not in p]
        cfg["cut_cross_entropy"] = False
    resolved = OUT / f"config.resolved{SUFFIX}.yaml"
    resolved.write_text(yaml.safe_dump(cfg, sort_keys=False))
    sh(["axolotl", "train", resolved, "--launcher", "python"], f"train{SUFFIX}.log", env=env, cwd=CODE,
       timeout=args.train_timeout)
    if (OUT / f"deadline_stop{SUFFIX}.json").exists():
        res_stop = json.loads((OUT / f"deadline_stop{SUFFIX}.json").read_text())
        log(f"training stopped at the time budget: {res_stop}")
    steps = [json.loads(l) for l in open(OUT / "steps.jsonl")] if (OUT / "steps.jsonl").exists() else []
    st = [s for s in steps if s.get("kind") == "step"]
    res = {"steps": len(st)}
    if len(st) > 3:
        warm = st[2:]  # skip the first steps (optimizer state creation, compilation)
        dt = (warm[-1]["t"] - warm[0]["t"]) / max(1, len(warm) - 1)
        toks = (warm[-1]["tokens_cum"] - warm[0]["tokens_cum"]) / max(1e-9, warm[-1]["t"] - warm[0]["t"])
        rows = (warm[-1]["rows_cum"] - warm[0]["rows_cum"]) / max(1e-9, warm[-1]["t"] - warm[0]["t"])
        res.update(s_per_step=round(dt, 3), tokens_per_s=round(toks, 1), rows_per_s=round(rows, 3),
                   s_per_10k_rows=round(1e4 / rows, 0) if rows else None)
    losses = [s for s in steps if s.get("kind") == "log" and "loss" in s]
    if losses:
        res["loss_first"], res["loss_last"] = losses[0]["loss"], losses[-1]["loss"]
    return res


train = stage("train")(_train)
train_retry_mb1 = stage("train_retry_mb1")(_train)
train_retry_plain = stage("train_retry_plain")(_train)


def train_ok() -> bool:
    return any(STATE["stages"].get(k, {}).get("status") == "ok" for k in ("train", "train_retry_plain", "train_retry_mb1"))


def n_steps_logged() -> int:
    p = OUT / "steps.jsonl"
    return sum(1 for l in open(p) if '"kind": "step"' in l) if p.exists() else 0


def find_adapter(root=None) -> Path:
    root = Path(root) if root is not None else OUT / "checkpoints"
    if (root / "adapter_model.safetensors").exists():
        return root
    cks = sorted(root.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[1]))
    if cks and (cks[-1] / "adapter_model.safetensors").exists():
        return cks[-1]
    raise FileNotFoundError("no adapter_model.safetensors under checkpoints/")


def is_ddp():
    return int(os.environ.get("WORLD_SIZE", "1")) > 1


def prepare_local_output(work: Path) -> Path:
    """Use a fresh local directory; DDP resume is deliberately unsupported."""
    probe = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "--target", str(work)],
                           check=True, capture_output=True, text=True).stdout.strip()
    if probe not in {"ext4", "xfs", "overlay"}:
        raise RuntimeError(f"unsupported local output filesystem: {probe}")
    free = shutil.disk_usage(work).free
    if free < MIN_LOCAL_FREE:
        raise RuntimeError(f"local output has {free} bytes free; requires at least {MIN_LOCAL_FREE}")
    path = work / f"train-output-rank{RANK}-{uuid.uuid4().hex}"
    path.mkdir(mode=0o700, exist_ok=False)
    STATE["local_train_output"] = str(path)
    STATE["local_output_fs"] = probe
    STATE["local_free_bytes"] = free
    STATE["local_checkpoint_limit"] = 3
    STATE["resume_limit"] = "Abrupt termination before persistence may lose local checkpoints."
    save_state()
    return path


def persist_local_output(local: Path) -> Path:
    """Copy a fresh rank-0 tree, then verify every regular file before marking usable."""
    checkpoints = [p for p in local.glob("checkpoint-*") if p.is_dir()]
    if len(checkpoints) > 3:
        raise ValueError("too many local checkpoints to persist")
    source_adapter = local  # DDP requires the final adapter, not a saved intermediate checkpoint.
    for required in (source_adapter / "adapter_model.safetensors", source_adapter / "adapter_config.json",
                     local / "tokenizer.json", local / "tokenizer_config.json"):
        if not required.is_file() or required.is_symlink():
            raise FileNotFoundError(f"required local output missing: {required}")
    files = []
    for path in sorted(local.rglob("*")):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ValueError(f"unsupported output entry: {path}")
        if path.is_file():
            files.append((path.relative_to(local), path.stat().st_size, sha256(path)))
    STATE["local_output_bytes"] = sum(size for _, size, _ in files)
    STATE["local_free_bytes_at_persistence"] = shutil.disk_usage(local).free
    save_state()
    destination = OUT / "checkpoints" / ("verified-" + uuid.uuid4().hex)
    destination.parent.mkdir(exist_ok=True)
    destination.mkdir(exist_ok=False)
    for relative, size, digest in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local / relative, target)
        if target.stat().st_size != size or sha256(target) != digest:
            raise IOError(f"persistent output verification failed: {relative}")
    copied = sorted(p.relative_to(destination) for p in destination.rglob("*") if p.is_file())
    if copied != sorted(row[0] for row in files):
        raise IOError("persistent output file inventory mismatch")
    persisted_adapter = destination / source_adapter.relative_to(local)
    marker = destination / "verified-persistence.json"
    marker_payload = {"files": [{"path": str(r), "bytes": n, "sha256": h} for r, n, h in files],
                      "adapter": str(persisted_adapter.relative_to(destination))}
    temporary_marker = destination / (".verified-" + uuid.uuid4().hex + ".tmp")
    temporary_marker.write_text(json.dumps(marker_payload, indent=1))
    os.replace(temporary_marker, marker)
    if json.loads(marker.read_text()) != marker_payload:
        raise IOError("persistent marker readback mismatch")
    STATE["verified_persisted_adapter"] = str(persisted_adapter)
    STATE["verified_persistence_marker"] = str(marker)
    save_state()
    return persisted_adapter


def publish_terminal(status: str, detail: str = ""):
    """Rank zero publishes a single terminal result; no failure is labeled success."""
    payload = {"status": status, "detail": detail, "finished_unix": int(time.time())}
    tmp = OUT / ("rank0-terminal-" + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, OUT / "rank0-terminal.json")


@stage("eval_teacher")
def eval_teacher(args, env, adapter):
    sh([sys.executable, SFT / "eval_sft.py", "teacher", "--snapshot", args.snapshot, "--adapter", adapter,
        "--eval", Path(args.data) / "eval.jsonl.gz", "--n", args.eval_n, "--gen-n", args.gen_n,
        "--out", OUT / "eval_teacher.json"], "eval_teacher.log", env=env, cwd=CODE, timeout=2400)
    d = json.loads((OUT / "eval_teacher.json").read_text())
    return {"tuned": d.get("tuned"), "base_same_prefix": d.get("base_same_prefix"), "hf_generation": d.get("hf_generation")}


@stage("convert_lora_gguf")
def convert(args, env, adapter, pre, shm):
    while not pre.get("done"):
        time.sleep(5)
    if "llama_src" not in pre:
        raise RuntimeError(f"llama.cpp prefetch failed: {pre.get('error')}")
    src = Path(pre["llama_src"])
    conv = src / "convert_lora_to_gguf.py"
    patched = src / "convert_lora_to_gguf_hrsft.py"
    text = conv.read_text()
    shim = "class LoraTorchTensor:\n    ndim = property(lambda self: len(self.shape))  # hrsft: Muse rope un-permute uses .ndim\n"
    if "class LoraTorchTensor:\n" not in text:
        raise RuntimeError("convert_lora_to_gguf.py layout changed")
    patched.write_text(text.replace("class LoraTorchTensor:\n", shim, 1))
    out = shm / "adapter-bf16.gguf"
    e = dict(env, PYTHONPATH=f"{src / 'gguf-py'}:{src}")
    sh([sys.executable, patched, "--base", args.snapshot, "--outtype", "bf16", "--outfile", out, adapter],
       "convert.log", env=e, cwd=src, timeout=1800)
    shutil.copy(out, OUT / "adapter-bf16.gguf")
    return {"adapter_gguf": str(out), "bytes": out.stat().st_size, "sha256": sha256(out)}


def start_server(binary: Path, model: str, lora: str | None, log_name: str):
    cmd = [binary, "--model", model, *SERVER_ARGS] + (["--lora", lora] if lora else [])
    lf = open(OUT / log_name, "w")
    proc = subprocess.Popen(list(map(str, cmd)), stdout=lf, stderr=subprocess.STDOUT)
    t = time.time()
    while time.time() - t < 900:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited {proc.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                if r.status == 200:
                    log(f"llama-server ready in {time.time() - t:.0f}s (lora={bool(lora)})")
                    return proc
        except Exception:  # noqa: BLE001
            time.sleep(2)
    proc.kill()
    raise RuntimeError("llama-server not ready in 900 s")


@stage("serve_eval")
def serve_eval(args, env, pre, lora_gguf):
    while not pre.get("done"):
        time.sleep(5)
    if "gguf" not in pre:
        raise RuntimeError(f"prefetch failed: {pre.get('error')}")
    binary = Path(pre["llama_src"]) / "build" / "bin" / "llama-server"
    res = {}
    runs = [("lora", lora_gguf, args.serve_n, True)] if lora_gguf else []
    if args.base_n:
        runs.append(("base", None, args.base_n, False))
    for label, lora, n, raw in runs:
        proc = start_server(binary, pre["gguf"], lora, f"llama_server_{label}.log")
        try:
            cmd = [sys.executable, SFT / "eval_sft.py", "serve", "--url", f"http://127.0.0.1:{PORT}",
                   "--tokenizer-dir", args.snapshot, "--eval", Path(args.data) / "eval.jsonl.gz", "--n", n,
                   "--label", label, "--out", OUT / f"eval_serve_{label}.json"] + (["--raw"] if raw else [])
            sh(cmd, f"eval_serve_{label}.log", env=env, cwd=CODE, timeout=3600)
            d = json.loads((OUT / f"eval_serve_{label}.json").read_text())
            res[label] = {"chat": d["chat"], "raw_no_grammar": d.get("raw_no_grammar")}
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
    return res


def main():
    global OUT, LOCAL_OUTPUT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--epochs", type=int, default=None)
    ap.add_argument("--micro-batch", type=int, default=None)
    ap.add_argument("--grad-accum", type=int, default=None)
    ap.add_argument("--lr", type=float, default=None)
    ap.add_argument("--lora-r", type=int, default=None)
    ap.add_argument("--lora-alpha", type=int, default=None)
    ap.add_argument("--warmup-steps", type=int, default=None)
    ap.add_argument("--save-steps", type=int, default=None)
    ap.add_argument("--train-timeout", type=int, default=None,
                    help="hard timeout of the training subprocess (default: derived from --job-timeout)")
    ap.add_argument("--job-timeout", type=int, default=10800, help="the AML job's limits.timeout (submit_sft.sh passes it)")
    ap.add_argument("--post-budget", type=int, default=None,
                    help="seconds reserved after training for eval/convert/serve (default 2400, 300 if both skipped)")
    ap.add_argument("--resume-from", default=None, help="checkpoint dir to resume training from")
    ap.add_argument("--eval-only", action="store_true", help="no training: evaluate/convert/serve --adapter")
    ap.add_argument("--adapter", default=None, help="adapter (checkpoint) dir for --eval-only")
    ap.add_argument("--eval-n", type=int, default=200)
    ap.add_argument("--gen-n", type=int, default=24)
    ap.add_argument("--serve-n", type=int, default=200)
    ap.add_argument("--base-n", type=int, default=30)
    ap.add_argument("--skip-serve", action="store_true")
    ap.add_argument("--skip-teacher", action="store_true")
    ap.add_argument("--no-mb-fallback", action="store_true")
    ap.add_argument("--loss", choices=["plain", "weighted"], default="weighted",
                    help="plain: token-mean CE (CCE); weighted: choice-token weight W + soft policy target (sft_plugin)")
    ap.add_argument("--choice-weight", type=float, default=10.0)
    ap.add_argument("--soft-alpha", type=float, default=0.7)
    args = ap.parse_args()
    args.data = str(Path(args.data).resolve())
    args.snapshot = str(Path(args.snapshot).resolve())
    OUT = Path(args.out)
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path("/tmp/hrsft")
    shm = Path("/dev/shm/hrsft") if Path("/dev/shm").exists() and shutil.disk_usage("/dev/shm").free > 40e9 else work / "shm"
    for d in (work, shm, work / "tmp", work / "hf"):
        d.mkdir(parents=True, exist_ok=True)
    STATE.update(args=vars(args), started_unix=int(time.time()), shm=str(shm))
    env = dict(os.environ, TMPDIR=str(work / "tmp"), HF_HOME=str(work / "hf"), HF_DATASETS_CACHE=str(work / "hf" / "ds"),
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", WANDB_MODE="offline",
               WANDB_DIR=str(work), TOKENIZERS_PARALLELISM="false", PYTHONUNBUFFERED="1",
               HRSFT_STEPS_LOG=str(OUT / "steps.jsonl"), HRSFT_LOSS=args.loss,
               HRSFT_CHOICE_W=str(args.choice_weight), HRSFT_SOFT_ALPHA=str(args.soft_alpha),
               PYTHONPATH=os.pathsep.join([str(SFT), str(TOOLS), str(CODE), os.environ.get("PYTHONPATH", "")]))
    if int(os.environ.get("WORLD_SIZE", "1")) > 1:
        # NC40ads_H100_v5 nodes have no InfiniBand: NCCL over TCP sockets. LoRA grads are ~0.4 GB/step.
        env.update(NCCL_IB_DISABLE="1", NCCL_DEBUG="WARN", TORCH_NCCL_ASYNC_ERROR_HANDLING="1")
    if is_ddp() and args.resume_from:
        raise RuntimeError("DDP resume is not supported by local-output fallback")
    if is_ddp() and RANK == 0 and ((OUT / "rank0-terminal.json").exists() or (OUT / "checkpoints").exists()):
        raise RuntimeError("DDP requires a fresh persistent output directory")
    if RANK == 0 and (OUT / "rank0.done").exists():
        (OUT / "rank0.done").unlink()  # stale marker from an earlier run in the same output dir
    if preflight(args) is None:
        sys.exit(2)
    if is_ddp() and not args.eval_only:
        LOCAL_OUTPUT = prepare_local_output(work)
    # Time budget (identical on every rank, so the all-reduced graceful stop agrees).
    post = args.post_budget if args.post_budget is not None else (300 if args.skip_serve and args.skip_teacher else 2400)
    if args.train_timeout is None:
        args.train_timeout = int(args.job_timeout - (time.time() - STATE["started_unix"]) - post - 600)
    graceful = time.time() + args.train_timeout - 900
    env["HRSFT_TRAIN_DEADLINE"] = str(int(graceful))
    STATE["time_budget"] = {"job_timeout": args.job_timeout, "post_budget": post, "train_timeout": args.train_timeout,
                            "graceful_stop_unix": int(graceful)}
    log(f"time budget: {STATE['time_budget']}")
    if args.train_timeout < 1800 and not args.eval_only:
        log("WARNING: less than 30 min of training budget")
    pre: dict = {}
    if RANK != 0:  # multi-node DDP: non-zero ranks only train; rank 0 saves, evaluates and serves
        args.skip_serve = args.skip_teacher = True
    if not args.skip_serve:
        threading.Thread(target=prefetch, args=(work, shm, pre), daemon=True).start()
    if args.eval_only:
        if not args.adapter or not (Path(args.adapter) / "adapter_model.safetensors").exists():
            raise SystemExit("--eval-only needs --adapter <dir with adapter_model.safetensors>")
        STATE["mode"] = "eval-only"
        run_post(args, env, Path(args.adapter), pre, shm)
        if all(STATE["stages"].get(k, {}).get("status") != "ok" for k in ("eval_teacher", "serve_eval")):
            sys.exit(4)
        return
    train(args, env)
    if is_ddp():
        # No rank-local objective or batch-size changes in a collective run.
        if STATE["stages"].get("train", {}).get("status") != "ok":
            STATE["fatal"] = "train failed on this rank"
            save_state()
            raise SystemExit(3)
        if RANK != 0:
            save_state()
            raise SystemExit(0 if wait_for_rank0() else 4)
        try:
            adapter = persist_local_output(LOCAL_OUTPUT)
        except Exception as exc:
            STATE["fatal"] = f"persistence failed: {type(exc).__name__}: {exc}"
            save_state()
            raise SystemExit(3)
        run_post(args, env, adapter, pre, shm)
        required = ([] if args.skip_teacher else ["eval_teacher"]) + (
            [] if args.skip_serve else ["convert_lora_gguf", "serve_eval"])
        if any(STATE["stages"].get(k, {}).get("status") != "ok" for k in required):
            raise SystemExit(4)
        return
    mb = args.micro_batch or 4
    if STATE["stages"]["train"]["status"] != "ok" and n_steps_logged() == 0 and args.loss == "weighted" \
            and not args.no_mb_fallback:
        log("weighted-loss training failed before the first step; retrying with --loss plain (see summary)")
        args.loss = "plain"
        env["HRSFT_LOSS"] = "plain"
        STATE["loss_fallback"] = "weighted -> plain"
        train_retry_plain(args, env)
        if STATE["stages"]["train_retry_plain"]["status"] == "ok":
            mb = 1
    if not train_ok() and n_steps_logged() == 0 and mb > 1 and not args.no_mb_fallback:
        log(f"train failed before the first step with micro-batch {mb}; retrying with micro-batch 1")
        args.grad_accum, args.micro_batch = (args.grad_accum or 4) * mb, 1
        train_retry_mb1(args, env)
    try:
        try:
            adapter = find_adapter()
        except FileNotFoundError as e:
            STATE["fatal"] = str(e)
            save_state()
            sys.exit(3)
        run_post(args, env, adapter, pre, shm)
        if not train_ok() and STATE["stages"].get("eval_teacher", {}).get("status") != "ok":
            sys.exit(4)
    finally:
        (OUT / "rank0.done").write_text(str(int(time.time())))


def run_post(args, env, adapter: Path, pre: dict, shm: Path):
    STATE["adapter_dir"] = str(adapter)
    if not args.skip_teacher:
        eval_teacher(args, env, adapter)
    if not args.skip_serve:
        conv = convert(args, env, adapter, pre, shm)
        STATE["prefetch"] = {k: v for k, v in pre.items() if k != "done"}
        serve_eval(args, env, pre, conv["adapter_gguf"] if conv else None)
    STATE["finished_unix"] = int(time.time())
    STATE["prefetch"] = {k: v for k, v in pre.items() if k != "done"}
    save_state()
    log("DONE " + json.dumps({k: v.get("status") for k, v in STATE["stages"].items()}))


def wait_for_rank0(poll: int = 30):
    """A nonzero rank succeeds only on an explicit successful terminal result."""
    deadline = STATE["started_unix"] + int(STATE["args"].get("job_timeout") or 10800) - 120
    log("waiting for rank 0 terminal result")
    while time.time() < deadline:
        marker = OUT / "rank0-terminal.json"
        if marker.exists():
            try:
                status = json.loads(marker.read_text()).get("status")
            except (OSError, ValueError, AttributeError):
                return False
            return status == "success"
        time.sleep(min(poll, max(0, deadline - time.time())))
    log("job deadline reached while waiting for rank 0")
    return False


def entrypoint():
    try:
        main()
    except BaseException as exc:
        if is_ddp() and RANK == 0 and "OUT" in globals():
            publish_terminal("failure", f"{type(exc).__name__}: {exc}")
        raise
    else:
        if is_ddp() and RANK == 0:
            publish_terminal("success")


if __name__ == "__main__":
    entrypoint()
