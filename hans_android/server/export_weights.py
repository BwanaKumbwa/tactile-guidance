"""
export_weights.py — Change 4: Export YOLO weights for faster inference.

Wraps the YOLOv5 export.py script to produce optimised model formats:
  torchscript — Portable TorchScript (all platforms, moderate speedup)
  onnx        — ONNX graph (compatible with ONNX Runtime / TensorRT)
  engine      — TensorRT engine (NVIDIA GPUs only, 2–5× fastest option)

Usage:
  python export_weights.py                          # TorchScript (safe default)
  python export_weights.py --format onnx
  python export_weights.py --format engine          # TensorRT (NVIDIA only)
  python export_weights.py --format all             # produce all three
  python export_weights.py --format torchscript --no-half   # FP32 only

After export, update PipelineConfig in master.py:
  weights_obj  = 'weights/yolov5s.torchscript'      # or .onnx / .engine
  weights_hand = 'weights/hand_v5_optivist.torchscript'

DetectMultiBackend auto-detects the format — no other code changes required.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import torch

# ── Constants ────────────────────────────────────────────────────────────

YOLOV5_EXPORT = Path('C:/Code/tactile-guidance/hans_android/server/yolov5/export.py')

# Map of logical name → weight file relative path
WEIGHTS: Dict[str, str] = {
    'obj':  'C:/Code/tactile-guidance/hans_android/server/weights/yolov5s.pt',
    'hand': 'C:/Code/tactile-guidance/hans_android/server/weights/hand_v5_optivist.pt',
    'tracker': 'C:/Code/tactile-guidance/hans_android/server/weights/osnet_x0_25_market1501.pt',
}

# Output file extensions per format
EXTENSIONS: Dict[str, str] = {
    'torchscript': '.torchscript',
    'onnx':        '.onnx',
    'engine':      '.engine',
}

# Formats that support FP16 export
FP16_FORMATS = {'onnx', 'engine'}

# Formats that require CUDA
CUDA_FORMATS = {'engine'}


# ── Export logic ─────────────────────────────────────────────────────────

def check_prerequisites(formats: List[str]) -> bool:
    """Validate system prerequisites before starting long exports."""
    ok = True
    if not YOLOV5_EXPORT.exists():
        print(f'[Export] ❌  YOLOv5 export script not found: {YOLOV5_EXPORT}')
        ok = False
    for name, path in WEIGHTS.items():
        if not Path(path).exists():
            print(f'[Export] ⚠️  Weight file not found, will skip: {path}')
    if 'engine' in formats and not torch.cuda.is_available():
        print('[Export] ❌  TensorRT export requires a CUDA GPU. '
              'Remove --format engine or run on a CUDA machine.')
        ok = False
    return ok


def export_model(
    weight_path: str,
    fmt:         str,
    imgsz:       int  = 640,
    use_half:    bool = True,
    device:      str  = '0',
) -> Tuple[bool, Optional[Path]]:
    """
    Call YOLOv5 export.py as a subprocess for one weight file / format pair.
    Returns (success: bool, output_path: Path | None).
    """
    cmd = [
        sys.executable, str(YOLOV5_EXPORT),
        '--weights', weight_path,
        '--include', fmt,
        '--imgsz',   str(imgsz),
        '--device',  device if torch.cuda.is_available() else 'cpu',
    ]
    if use_half and fmt in FP16_FORMATS and torch.cuda.is_available():
        cmd.append('--half')
    if fmt == 'engine':
        cmd += ['--simplify']   # ONNX simplification before TRT conversion

    print(f'\n[Export] {Path(weight_path).name}  →  {fmt.upper()}')
    print(f'[Export] Running: {" ".join(cmd)}')
    t0 = time.time()

    result = subprocess.run(cmd, capture_output=True, text=True)

    elapsed = time.time() - t0
    if result.returncode == 0:
        out_path = Path(weight_path).with_suffix(EXTENSIONS[fmt])
        print(f'[Export] ✅  Done in {elapsed:.1f}s  →  {out_path}')
        return True, out_path
    else:
        print(f'[Export] ❌  Failed after {elapsed:.1f}s')
        if result.stderr:
            # Show last 20 lines of stderr for concise diagnostics
            lines = result.stderr.strip().split('\n')
            for line in lines[-20:]:
                print(f'         {line}')
        return False, None


def print_load_instructions(results: Dict[str, Tuple[bool, Optional[Path]]]) -> None:
    """Print PipelineConfig snippet for successfully exported weights."""
    successful = {k: v[1] for k, v in results.items() if v[0] and v[1]}
    if not successful:
        return

    print('\n' + '═' * 60)
    print('  Update PipelineConfig in master.py to use exported weights:')
    print('═' * 60)

    # Group by format for cleaner output
    by_format: Dict[str, Dict[str, Path]] = {}
    for key, path in successful.items():
        name, fmt = key.rsplit('_', 1)
        by_format.setdefault(fmt, {})[name] = path

    for fmt, paths in sorted(by_format.items()):
        print(f'\n  # {fmt.upper()}')
        for name, path in sorted(paths.items()):
            attr = f'weights_{name}'
            print(f"  {attr:<20} = '{path}'")

    print('\n  DetectMultiBackend auto-detects the format — no other changes needed.')
    print('═' * 60 + '\n')


def benchmark(weight_path: str, fmt: str, imgsz: int = 640, runs: int = 50) -> None:
    """
    Quick latency benchmark comparing .pt vs exported format.
    Loads both models in-process and times `runs` forward passes.
    """
    try:
        import sys
        for _p in ['/yolov5']:
            if _p not in sys.path:
                sys.path.append(_p)
        from yolov5.models.common import DetectMultiBackend
        from yolov5.utils.torch_utils import select_device
        import numpy as np

        device = select_device('0' if torch.cuda.is_available() else 'cpu')
        use_fp16 = torch.cuda.is_available() and fmt in FP16_FORMATS
        exported_path = str(Path(weight_path).with_suffix(EXTENSIONS[fmt]))

        dummy = torch.randn(1, 3, imgsz, imgsz, dtype=torch.float16 if use_fp16 else torch.float32)
        dummy = dummy.to(device)

        times: Dict[str, List[float]] = {}
        for label, wpath in [('original (.pt)', weight_path), (f'exported ({fmt})', exported_path)]:
            if not Path(wpath).exists():
                continue
            model = DetectMultiBackend(wpath, device=device, fp16=use_fp16)
            model.warmup(imgsz=(1, 3, imgsz, imgsz))
            t_list = []
            for _ in range(runs):
                t0 = time.perf_counter()
                model(dummy)
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                t_list.append((time.perf_counter() - t0) * 1000)
            times[label] = t_list
            print(f'  {label:<30}  avg={np.mean(t_list):.1f}ms  '
                  f'min={np.min(t_list):.1f}ms  '
                  f'p95={np.percentile(t_list, 95):.1f}ms')

        if len(times) == 2:
            keys   = list(times.keys())
            speedup = np.mean(times[keys[0]]) / np.mean(times[keys[1]])
            print(f'\n  Speedup: {speedup:.2f}×')

    except Exception as e:
        print(f'[Benchmark] Could not run benchmark: {e}')


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Export HANS YOLO weights for faster inference (Change 4).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--format', '-f',
        choices=['torchscript', 'onnx', 'engine', 'all'],
        default='torchscript',
        help='Export format (default: torchscript)',
    )
    parser.add_argument('--imgsz',    type=int,  default=640,
                        help='Inference image size (default: 640)')
    parser.add_argument('--no-half',  action='store_true',
                        help='Disable FP16 — export in FP32 instead')
    parser.add_argument('--device',   type=str,  default='0',
                        help='CUDA device index (default: 0)')
    parser.add_argument('--benchmark', action='store_true',
                        help='Run a quick latency benchmark after exporting')
    parser.add_argument('--weights',  nargs='+',
                        help='Override weight files (default: obj + hand)')
    args = parser.parse_args()

    formats = (['torchscript', 'onnx', 'engine']
               if args.format == 'all' else [args.format])
    use_half = not args.no_half

    if not check_prerequisites(formats):
        sys.exit(1)

    # Resolve weight files
    weight_map = WEIGHTS.copy()
    if args.weights:
        weight_map = {Path(w).stem: w for w in args.weights}

    # ── Export ────────────────────────────────────────────────────────────
    results: Dict[str, Tuple[bool, Optional[Path]]] = {}
    for name, path in weight_map.items():
        if not Path(path).exists():
            print(f'[Export] Skipping {name} ({path} not found)')
            continue
        for fmt in formats:
            key = f'{name}_{fmt}'
            results[key] = export_model(
                path, fmt, imgsz=args.imgsz,
                use_half=use_half, device=args.device)

    # ── Summary ───────────────────────────────────────────────────────────
    print('\n' + '─' * 40)
    print('Export Summary:')
    for key, (success, out) in results.items():
        status = '✅' if success else '❌'
        print(f'  {status}  {key}{"  →  " + str(out) if out else ""}')

    print_load_instructions(results)

    # ── Optional benchmark ────────────────────────────────────────────────
    if args.benchmark:
        print('\nBenchmark (50 runs, same GPU):')
        for name, path in weight_map.items():
            for fmt in formats:
                if results.get(f'{name}_{fmt}', (False,))[0]:
                    print(f'\n  Model: {name}  Format: {fmt}')
                    benchmark(path, fmt, imgsz=args.imgsz)


if __name__ == '__main__':
    main()
