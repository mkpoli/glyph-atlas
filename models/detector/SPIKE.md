# Installation spike, 2026-09-11

The spike time-boxes a day for this: try mmdetection with RTMDet-s against the PyTorch build the GPU
needs, and accept the spike only when one training step, a checkpoint reload and an ONNX inference
all worked. They did not with mmdetection. They did with RT-DETR through `transformers`, which is
what `config.yaml` now pins.

Machine: RTX 5070 Ti (Blackwell, 16 GB, driver 580.97), CUDA toolkit 13.0 at `/usr/local/cuda-13.0`,
gcc 13.3, 16 cores, Python 3.14.0 in `.venv` with no `pip` module, so every install went through
`uv pip install --python .venv/bin/python`.

## What was tried, in order

### 1. PyTorch on Blackwell under Python 3.14 — works

Python 3.14 has wheels from torch 2.9 on, so the framework is not the obstacle. Two builds were
installed and both ran on the card:

```sh
uv pip install --python .venv/bin/python --index-url https://download.pytorch.org/whl/cu128 "torch==2.11.0"
.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.get_device_capability(0))"
# torch 2.11.0+cu128 (12, 0) — CUDA 12.8 wheel, sm_120 runs
```

Installing `torchvision` afterwards moved the environment to the PyPI default build, which is what
the spike settled on and what `pyproject.toml` pins as the floor:

```sh
uv pip install --python .venv/bin/python transformers torchvision
.venv/bin/python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
# torch 2.14.0+cu130 13.0 True
```

```
$ .venv/bin/python -c "... torch.cuda.get_device_name(0) ..."
NVIDIA GeForce RTX 5070 Ti
capability (12, 0)
arch list ['sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120']
matmul ok -19861.796875
```

### 2. mmdetection with RTMDet-s — rejected, four blockers

**2a. `mmdet` installs, `mmcv` and `mmengine` do not come with it.** mmdet 3.3.0 declares its two
runtime dependencies behind the `mim` extra, so the plain install skips them and the import fails:

```sh
uv pip install --python .venv/bin/python "mmdet==3.3.0"      # exit 0, 12 packages
.venv/bin/python -c "import mmdet"
# ModuleNotFoundError: No module named 'mmcv'
```

**2b. No wheel for Python 3.14 exists.** mmcv publishes wheels only in its own channel, and that
channel stops well below this machine: `.../mmcv/dist/cu121/torch2.1.0/index.html` lists
`mmcv-2.2.0-cp38 … cp311` wheels, and `.../dist/cu124/torch2.2.0/`, `.../dist/cu128/torch2.9.0/`
and `.../dist/cu128/torch2.11.0/` all answer 404. PyPI has the sdist only, so a source build is the
only route, and mmcv 2.1.0 is the newest version mmdet 3.3.0 accepts (`mmcv <2.2.0`).

```sh
uv pip install --python .venv/bin/python "mmcv>=2.0.0rc4,<2.2.0" "mmengine>=0.7.1,<1.0.0"
```

**2c. The sdist does not configure under Python 3.14** (`logs/spike-mmcv-install.log`):

```
× Failed to build `mmcv==2.1.0`
╰─▶ Call to `setuptools.build_meta:__legacy__.build_wheel` failed (exit status: 1)
    File "<string>", line 5, in <module>
  ModuleNotFoundError: No module named 'pkg_resources'
```

setuptools 84, which uv puts in the build environment, no longer ships `pkg_resources`. Re-running
with `--no-build-isolation` against the venv's setuptools gets past that import but fails in mmcv's
own code (`logs/spike-mmcv-nobi.log`):

```
      <string>:5: DeprecationWarning: pkg_resources is deprecated as an API.
        File "<string>", line 450, in <module>
        File "<string>", line 43, in get_version
      KeyError: '__version__'
```

`mmcv/setup.py` line 43 is `return locals()['__version__']` after an `exec` of `mmcv/version.py`.
PEP 667 makes `locals()` a snapshot in Python 3.13 and later, so the name the `exec` bound is no
longer visible and the build dies before it compiles anything:

```
$ .venv/bin/python -c "def f(): exec(compile('__version__ = \"2.1.0\"', 'v', 'exec')); return locals()['__version__']" 
KeyError '__version__'   # Python 3.14.0
```

**2d. Patched, it does not compile against a current PyTorch.** Two patches were applied to a copy
of the sdist at `/tmp/mmcv-build/mmcv-2.1.0`: `get_version` reads the `exec` namespace explicitly,
and the `pkg_resources` import is replaced with `importlib.metadata` and `packaging.version`. The
build then reaches the compiler and stops on the first translation unit
(`logs/spike-mmcv-patched-build.log`):

```
./mmcv/ops/csrc/pytorch/active_rotated_filter.cpp:5:
  .../torch/include/ATen/ATen.h:5:2: error: #error C++20 or later compatible
  compiler is required to use ATen.
```

torch 2.14 requires C++20; `mmcv/setup.py` line 388 passes `extra_compile_args['cxx'] = ['-Wall',
'-std=c++17']`, which replaces the standard torch's `cpp_extension` would have used. mmcv 2.1.0 was
released for torch ≤ 2.1.

**2e. With `-std=c++20` forced it compiles, slowly, and was stopped.** Forcing `-std=c++20` for both
`cxx` and `nvcc` in the same patched copy produced a build that was still working after 100 minutes:
77 of the 135 C++ and CUDA sources under `mmcv/ops/csrc/pytorch/` had been compiled for
`compute_120/sm_120` with no error (`logs/spike-mmcv-cxx20.log`). It was stopped there to release the
`uv` environment lock for the ONNX part of the spike. Even a clean finish would leave mmdet 3.3.0,
which is a 2023 release written against torch ≤ 2.1, to import and train on torch 2.14, and its ONNX
export runs through `mmdeploy`, a second source build of the same kind.

**Verdict: mmdetection with RTMDet-s is not viable within the spike's time box on this machine.**

### 3. RT-DETR through `transformers` — accepted

`RTDetrForObjectDetection` with the `PekingU/rtdetr_r18vd` checkpoint (Apache-2.0, 20.1M
parameters, ResNet-18), `num_labels: 1` for a class-agnostic detector and `num_queries: 600`. The
spike's three acceptance items, each run on this machine's hardware:

**One training step.**

```sh
HF_HOME=$PWD/cache/huggingface .venv/bin/python models/detector/train.py \
    --config models/detector/config.yaml --data <split> --out <dir> --profile --profile-steps 6
# PekingU/rtdetr_r18vd on cuda, 20.1M parameters, num_queries 600, bf16
# epoch 0 step 6/6 loss 58.2452 0.80s/it peak 4.14GiB
# profiled 6 steps at batch 4, precision bf16, peak 4.14GiB
```

The classification head is reinitialised for one label — `transformers` reports the mismatched keys
(`decoder.class_embed.*`, `enc_score_head.*`, `denoising_class_embed.weight`) and loads the rest.
Peak VRAM by batch, 1024² tiles, bfloat16, `num_queries` 600: batch 4 → 4.14 GiB, batch 8 →
7.77 GiB, batch 12 → 11.40 GiB, batch 16 → 14.78 GiB. Batch 8 with two accumulation steps is what
`config.yaml` sets.

The numbers above come from a synthetic split built in `/tmp` for the spike: six generated pages of
1200x1500 with 50 px black squares, cut into 24 tiles of 1024² with the detector's training data
build geometry, written as
COCO with `origin`, `cache_path`, `source_unit_id` and one `iscrowd` region. That is enough to run
the training loop, the metrics and the checkpoints; the wall clock and the metrics on real pages wait
for `work/detector/`.

**Checkpoint reload.**

```sh
.venv/bin/python models/detector/train.py ... --epochs 3 --resume <dir>/last.pt
# resumed <dir>/last.pt at epoch 2
# epoch 2 step 6/6 loss 57.6738 0.92s/it peak 4.21GiB
```

`last.pt` carries the model, the optimizer and the scheduler state and the epoch, and the run
continues from the next one. `best.pt` is kept by validation F1.

**ONNX inference.**

```sh
.venv/bin/python models/detector/export_onnx.py --base --out /tmp/base.onnx --parity 1 --parity-score 0.05
# {"dynamo": false, "opset": 17, "size": 1024, "bytes": 81170177, "outputs": ["logits", "pred_boxes"]}
# {"matched": 39, "beyond_tolerance": 0, "only_torch": 1, "only_onnx": 1,
#  "max_corner_px": 0.067, "max_score_difference": 0.0007, "passed": true}
```

The legacy exporter writes one self-contained 81 MB file with the two outputs the detector decodes;
`onnx.checker` accepts it and `onnxruntime` 1.30 loads it on the CUDA provider. The dynamo exporter
was tried as well and works, but writes the weights to a separate `.onnx.data` file (2.3 MB +
82 MB), so the legacy path is what the scripts use.

```sh
.venv/bin/python -c "
from PIL import Image; from glyph_atlas.detect import Detector
d = Detector('/tmp/base.onnx', score=0.05)
print(d.providers()); print(len(d.boxes(Image.open('<page>.png'))))
"
# ['CUDAExecutionProvider', 'CPUExecutionProvider']
# 103 boxes in 0.56s on a 1200x1500 page
```

`onnxruntime-gpu` needs `onnxruntime.preload_dlls()` before a CUDA session on this machine: cuDNN
comes from the `nvidia-cudnn-cu13` package and the provider otherwise reports
`dlopen failed for libcudnn.so`. `Detector` calls it.

One property of the export is worth recording, because it shapes the parity test. The decoder's
queries have no fixed meaning: the encoder picks its `topk` tokens from scores that trace to within
about 1e-4 but not exactly, so a near tie can send a query to a different reference point on either
side. Measured with the base checkpoint on one tile of a synthetic page: at score ≥ 0.1, 2 PyTorch
detections and 2 ONNX detections, both pairs matched, largest corner difference 0.011 px; at score
≥ 0.05, 40 against 41, 39 matched, largest corner difference 0.054 px. The pairs that do not match
are single queries that flipped, which is why `export_onnx.py --parity` compares the two sets of
detections and allows a small unmatched count. On a checkpoint trained for two epochs on 24
synthetic tiles the same comparison matches almost nothing — every query of an untrained model is a
near tie — so the parity check is meaningful only once the detector is trained.

## Versions the spike settled on

| Package | Version |
| --- | --- |
| Python | 3.14.0 |
| torch | 2.14.0+cu130 |
| torchvision | 0.29.0+cu130 |
| transformers | 5.17.0 |
| tokenizers | 0.23.2 |
| safetensors | 0.8.0 |
| onnx | 1.22.0 |
| onnxscript | 0.7.2 |
| onnxruntime-gpu | 1.30.0 |
| numpy | 2.5.3 |
| pillow | 12.3.0 |
| PyYAML | 6.0.3 |
| CUDA | 13.0 (toolkit 13.0.88, driver 580.97) |

## Decision

RT-DETR (`PekingU/rtdetr_r18vd`, revision `ac77a11ff0170a41b771c03264987f8ce2b0d753`) through
`transformers`, pinned in `config.yaml` with a `detector` extra in `pyproject.toml`. mmdetection is
out: no `mmcv` wheel and no source build that configures, compiles and would then run
on Python 3.14 with a current PyTorch.

Nothing in the spike was blocked by the training data not existing yet; `train.py`, `export_onnx.py`, `detect.py`
and `tests/test_detect.py` were exercised on synthetic tiles and on a tiny exported ONNX model built
in the test, and the real training waits for `work/detector/`.
