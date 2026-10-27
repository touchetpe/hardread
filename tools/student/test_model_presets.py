"""Shape, parameter-count, and config-round-trip tests for student presets."""
from pathlib import Path
import os
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in HERE.parents
            if (parent / "tools" / "student" / "encode.py").is_file())
# Load the proposed model.py while reusing the unchanged encoder schema.
sys.path.insert(0, str(ROOT / "tools" / "student"))
sys.path.insert(0, str(HERE))

import torch  # noqa: E402
from encode import Encoder, F_ACT, F_ENT, F_EV, F_FIELD, TABLE_VOCAB  # noqa: E402
from model import StudentNet, build  # noqa: E402


def _synthetic_vocab_sizes():
    names = sorted(set(TABLE_VOCAB.values()))
    return {name: 13 + index for index, name in enumerate(names)}


def _linear_params(in_features, out_features):
    return in_features * out_features + out_features


def _expected_large_params(vocab_sizes):
    d, layers, ffn = 768, 12, 3072
    block = 4 * d * d + 2 * d * ffn + 9 * d + ffn
    total = layers * block
    total += sum(vocab_sizes[name] * d for name in TABLE_VOCAB.values())
    total += (4 + 6) * d  # action-kind and token-type embeddings
    total += sum(_linear_params(width, d) for width in (F_EV, F_ENT, F_FIELD, F_ACT))
    total += 4 * d  # input/output LayerNorms
    total += 3 * (_linear_params(d, d // 2) + _linear_params(d // 2, 1))
    total += 2 * _linear_params(d, d) + 2 * vocab_sizes["moves"]
    total += _linear_params(d, d // 2) + _linear_params(d // 2, 1)
    return total, layers * block


def _state_shapes(net):
    return {name: tuple(tensor.shape) for name, tensor in net.state_dict().items()}


class StudentPresetTests(unittest.TestCase):
    def test_large_preset_shapes_and_exact_parameter_count(self):
        vocab_sizes = _synthetic_vocab_sizes()
        with torch.device("meta"):
            net = build(vocab_sizes, size="large")

        self.assertEqual((net.config["d"], net.config["layers"], net.config["heads"], net.config["ffn"]),
                         (768, 12, 12, 3072))
        self.assertEqual(len(net.blocks), 12)
        block = net.blocks[0]
        self.assertEqual(tuple(block.qkv.weight.shape), (2304, 768))
        self.assertEqual(tuple(block.proj.weight.shape), (768, 768))
        self.assertEqual(tuple(block.fc1.weight.shape), (3072, 768))
        self.assertEqual(tuple(block.fc2.weight.shape), (768, 3072))
        self.assertEqual(tuple(net.emb["species"].weight.shape), (vocab_sizes["species"], 768))
        self.assertEqual(tuple(net.pol2.weight.shape), (1, 384))
        self.assertEqual(net.opp_bias.numel(), 2 * vocab_sizes["moves"])

        expected_total, expected_blocks = _expected_large_params(vocab_sizes)
        self.assertEqual(sum(parameter.numel() for parameter in net.blocks.parameters()), expected_blocks)
        self.assertEqual(net.n_params(), expected_total)
        self.assertGreater(net.n_params(), 85_000_000)

    def test_large_preset_actual_encoder_parameter_count(self):
        # Build only the local static encoder vocabulary; meta tensors allocate no model weights.
        vocab_sizes = Encoder().sizes()
        with torch.device("meta"):
            net = build(vocab_sizes, size="large")

        self.assertEqual(net.n_params(), 91_018_246)
        self.assertEqual(sum(parameter.numel() for parameter in net.blocks.parameters()), 85_054_464)

    def test_train_cli_exposes_large_without_changing_default(self):
        train_script = HERE / "train.py"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "tools" / "student")
        result = subprocess.run(
            [sys.executable, str(train_script), "--help"],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--size {base,small,tiny,large}", result.stdout)
        self.assertIn("--size", result.stdout)

        accepts_large = subprocess.run(
            [sys.executable, str(train_script), "--size", "large", "--help"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(accepts_large.returncode, 0, accepts_large.stderr)

    def test_existing_base_config_round_trips_with_identical_state_shapes(self):
        vocab_sizes = _synthetic_vocab_sizes()
        with torch.device("meta"):
            default = build(vocab_sizes)
            base = build(vocab_sizes, size="base")
            restored = StudentNet(**base.config)

        self.assertEqual((base.config["d"], base.config["layers"], base.config["heads"], base.config["ffn"]),
                         (512, 7, 8, 2048))
        self.assertEqual(default.config, base.config)
        self.assertEqual(base.config["vocab_sizes"], vocab_sizes)
        restored.load_state_dict(base.state_dict(), strict=True)
        self.assertEqual(_state_shapes(base), _state_shapes(restored))


if __name__ == "__main__":
    unittest.main()
