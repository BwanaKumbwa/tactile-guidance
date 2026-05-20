# HANS - Automated Hand Navigation System

An AI-powered assistive technology system that enables blind users to autonomously grasp objects using tactile feedback guidance via a wearable bracelet. The system combines real-time vision processing (YOLOv5 object detection & StrongSORT tracking) with intelligent haptic navigation through a tactile interface.

## Overview

HANS bridges computer vision and haptic feedback to guide users' hands toward target objects. A Kotlin Android app sends camera frames to a Python server, which processes them in real-time and returns haptic guidance patterns via a tactile bracelet.

**Key Innovation:** The system learns hand position and object location, then provides directional vibration patterns (left/right/up/down) to guide the user's grasp with minimal latency.

## Features

- **Real-time Object Detection & Tracking** — YOLOv5-based detection with StrongSORT multi-object tracking
- **Multi-Hand Support** — Detects and tracks both hands independently
- **Depth Estimation** — Hardware depth from Android phone or ML-based fallback (MIDAS/UniDepth)
- **Tactile Feedback System** — Haptic guidance via wearable bracelet (200ms BLE throttle)
- **LLM-Powered Control** — MCP server integration for voice commands and AI decisions
- **Android Integration** — Real-time frame streaming from Kotlin app via WebSocket
- **User Studies Framework** — Built-in experiment runner with CSV data logging
- **Dual-Mode Operation** — Standalone research mode or server-based deployment

## Project Structure

```
hans_android/                                    # Project root
├── tests/                                       # Test suite (currently 22 tests)
│   ├── __init__.py
│   ├── unit/                                    # Component tests
│   │   ├── test_auditory_interface.py          # MCP server & Android bridge tests
│   │   ├── test_android_source.py              # Frame streaming tests
│   │   └── test_vision_to_haptic.py            # Vision-haptic integration tests
│   ├── integration/                            # End-to-end tests
│   │   ├── test_android_communication.py       # Android-server communication
│   └── conftest.py                             # Pytest configuration & fixtures
│
├── server/                                     # Python server (core HANS logic)
│   ├── __init__.py
│   ├── vision_pipeline.py                      # Pure vision processing pipeline
│   ├── master.py                               # System orchestration & entry point
│   ├── controller.py                           # Legacy controller (deprecated)
│   ├── bracelet.py                             # Tactile bracelet/belt control
│   ├── feedback_device.py                      # Haptic feedback driver
│   ├── experiment_runner.py                    # User study trial management
│   ├── shared_state.py                         # Inter-thread state synchronization
│   ├── labels.py                               # COCO object class labels
│   ├── auto_connect.py                         # Device connection utilities
│   ├── depth_navigation_functions.py           # Depth-based path planning
│   │
│   ├── auditory_interface/                     # MCP Server + Android Bridge
│   │   ├── server_hans.py                      # MCP server with FastMCP tools
│   │   ├── server_main.py                      # FastAPI server orchestration
│   │   ├── android_loader.py                   # Android frame source
│   │   ├── query_processing.py                 # LLM query processor (HANSBrain)
│   │   ├── virtual_belt.py                     # WebSocket belt simulator
│   │   ├── mcp_config.py                       # MCP configuration
│   │   ├── audio_engine.py                     # Auditory signal processing
│   │   └── __init__.py
│   │
│   ├── helper_functions/                       # Utility functions
│   ├── yolov5/                                 # YOLOv5 detection models
│   ├── strongsort/                             # StrongSORT tracking
│   ├── midas/                                  # MIDAS depth estimation
│   ├── unidepth/                               # UniDepth depth estimation
│   ├── weights/                                # Pre-trained model weights
│   ├── results/                                # Experiment outputs & logs
│   ├── AGENTS.md                               # LLM agent specifications
│   ├── SKILLS.md                               # System capabilities documentation
│   ├── SOUL.md                                 # System design philosophy
│   └── __init__.py
│
├── android_client/                             # Kotlin Android App
│   └── ... (Kotlin source code)
│
├── __init__.py                                 # Root package marker
├── pytest.ini                                  # Test discovery configuration
environment.yaml                                # Conda environment specification
.env.example                                    # Environment variables template
.gitignore
README.md                                       # This file
LICENSE
```

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│           Kotlin Android App                            │
│  (Camera input, frame streaming via WebSocket)          │
└────────────────────┬────────────────────────────────────┘
                     │ Camera frames
                     ▼
     ┌─────────────────────────────────┐
     │   server_main.py (FastAPI)      │
     │   - Frame reception             │
     │   - State management            │
     │   - WebSocket connection        │
     └────────────┬────────────────────┘
                  │
     ┌────────────▼─────────────────────────────────────┐
     │      master.py (Orchestrator)                    │
     │  - Pipeline initialization                       │
     │  - Deployment vs. Research mode selection        │
     │  - Device connection management                  │
     └────────────┬───────────────────────────────────┬─┘
                  │                                   │
      ┌───────────▼──────────┐            ┌──────────▼─────────┐
      │ VisionPipeline       │            │  ExperimentRunner  │
      │ (12 processing       │            │  (Trial state      │
      │  stages)             │            │   machine + CSV)   │
      │                      │            │                    │
      │ 1. YOLOv5 inference  │            │ Research mode only │
      │ 2. StrongSORT track  │            │ (OpenCV display)   │
      │ 3. Hand detection    │            │                    │
      │ 4. Depth estimation  │            └────────────────────┘
      │ 5. Haptic guidance   │
      │ 6. Grasp detection   │
      │ 7. Memory persistence│
      │ 8. WebSocket publish │
      └───────────┬──────────┘
                  │
      ┌───────────▼─────────────────────┐
      │   BraceletController            │
      │  - Hand→target guidance         │
      │  - Vibration pattern generation │
      │  - 200ms BLE throttle           │
      │  - 1.5s post-grasp cooldown     │
      └───────────┬─────────────────────┘
                  │ Haptic commands
                  ▼
      ┌──────────────────────────┐
      │  Tactile Bracelet/Belt   │
      │  (Physical feedback)     │
      └──────────────────────────┘
                  │
                  ▼
              User Hand
          (Guided grasp)
```

## Installation

### Prerequisites

- **Python** 3.12+
- **Conda** (Miniconda or Anaconda)
- **Android smartphone** with USB connectivity for camera input
- **Tactile bracelet** hardware (optional; system includes virtual belt simulator)
- **GPU** (NVIDIA CUDA 12.8) for real-time performance; CPU mode available

### Setup

1. **Clone the repository**

```bash
git clone <repository-url>
```

2. **Create conda environment**

```bash
conda env create -f environment.yaml
conda activate hans_android
```

3. **Configure environment variables**

```bash
cp .env.example .env
# Edit .env with your API credentials and settings
# Copy the .env to auditory_interface
```

4. **Adjust IP in the Kotlin app**
In the MainActivity.kt, update SERVER_IP with your IPv4 address.
On Windows, it can be obtained by running ipconfig command in the Command Prompt.

## Usage

### Running Tests

To run the full test suite (22 tests):

```bash
# Run all tests
python -m pytest tests/ -v

# Run unit tests only
python -m pytest tests/unit/ -v

# Run integration tests only
python -m pytest tests/integration/ -v

# Run specific test
python -m pytest tests/unit/test_android_source.py -v

# Generate coverage report
python -m pytest tests/ --cov=server --cov-report=html
```

### Running the Server (Deployment Mode)

For production deployment with Android app:

```bash
# Start MCP server + FastAPI + Vision pipeline
python -m server.auditory_interface.server_main --mode deployment

# Or with debug output
python -m server.auditory_interface.server_main --mode deployment --debug

# Testing mode (visual output on the server terminal)
python -m server.auditory_interface.server_main --mode testing
```

The server:
- Listens for WebSocket connections from the Kotlin app
- Processes camera frames in real-time
- Sends haptic commands back to the bracelet
- Exposes MCP tools for LLM integration
- Provides REST endpoints for state queries

### Running Individual Components

**Vision pipeline only (no haptics):**

```bash
python -c "
from server.vision_pipeline import VisionPipeline, PipelineConfig
import queue

cfg = PipelineConfig(run_depth=True)
pipeline = VisionPipeline(cfg=cfg, mcp_queue=queue.Queue(), ...)
pipeline.start()
pipeline.wait()
"
```

**Bracelet/haptic testing:**

```bash
python -c "
from server.bracelet import BraceletController

bc = BraceletController(vibration_intensities={'top': 100, 'bottom': 50, 'left': 75, 'right': 25})
# Test vibration patterns
"
```

**Android frame streaming test:**

```bash
python -c "
from server.auditory_interface.android_loader import AndroidSource
import queue

frame_queue = queue.Queue(maxsize=1)
source = AndroidSource(frame_queue, img_size=640)
# Receives frames from Android app
"
```

## Core Components

### VisionPipeline (`server/vision_pipeline.py`)

Pure computer-vision processing engine with **12 pipeline stages**:

1. **YOLOv5 inference** (dual models: objects + hands)
2. **StrongSORT tracking** (multi-object ID assignment)
3. **Hand detection** (separate hand pose model)
4. **Depth resolution** (hardware or ML-based)
5. **Camera-shake filtering** (frame similarity check)
6. **Empty-crop filtering** (StrongSORT stability)
7. **Depth propagation** (track-based depth association)
8. **Visible object publishing** (state synchronization)
9. **Opportunistic target lock** (auto-focus unordered targets)
10. **Haptic engine** (guidance calculation)
11. **WebSocket dispatch** (client updates)
12. **Memory persistence** (per-participant state)

**Features:**
- CUDA stream parallelization (dual-GPU inference)
- FP16 precision support
- 200ms BLE throttling for haptic commands
- Per-participant vibration calibration
- Automatic memory save (5s interval)

### Master Controller (`server/master.py`)

System orchestrator supporting two execution modes:

- **`deployment_mode=True`** → Headless server (used by `server_main.py`)
- **`deployment_mode=False`** → Interactive research (uses `ExperimentRunner`)

Responsibilities:
- Load calibration data
- Initialize feedback devices (physical bracelet or virtual belt)
- Wire VisionPipeline components
- Route frame sources (Android or file)
- Manage experiment state

### BraceletController (`server/bracelet.py`)

Haptic guidance engine translating vision data to vibration patterns:

- **Hand→target vector**: Direction to target
- **Distance scaling**: Intensity decreases with distance
- **Grasp detection**: IoU-based overlap check
- **BLE throttling**: 200ms minimum between commands
- **Post-grasp cooldown**: 1.5s vibration lock after successful grasp

**Vibration zones:**
```
  [TOP]
[LEFT][RIGHT]
 [BOTTOM]
```

Each zone has intensity 0–100 (user-calibrated).

### ExperimentRunner (`server/experiment_runner.py`)

User study framework with trial state machine:

```
[IDLE] → 's' → [TRIAL_ACTIVE] → {'y','n','f','t'} → [TRIAL_COMPLETE] → 'c' → [SAVE & EXIT]
```

Features:
- Per-trial data collection (grasp time, distance, errors)
- CSV export with participant ID + condition
- Configurable target lists
- Manual or predefined target entry
- Integration with VisionPipeline (no hardcoding)

### Auditory Interface (`server/auditory_interface/`)

MCP server + Android communication bridge:

- **`server_hans.py`**: FastMCP server with LLM tools (set_target, control_vision, battery_saver, etc.)
- **`server_main.py`**: FastAPI orchestration, WebSocket frame reception, MCP lifecycle
- **`android_loader.py`**: Mimics YOLOv5 `LoadStreams` for frame queuing
- **`query_processing.py`**: HANSBrain LLM query processor
- **`virtual_belt.py`**: WebSocket-based tactile belt simulator for testing

## Configuration

Edit `.env` file:

```bash
# API & LLM
API_URL=http://your-gateway.com/api
API_KEY=your_api_key
LLM_MODEL=openai/gpt-4o-mini
```

## Dependencies

See `environment.yaml` for complete specification. Key packages:

| Category | Packages |
|----------|----------|
| **Deep Learning** | torch 2.0+, torchvision, torchaudio |
| **Vision** | opencv-python 4.5+, YOLOv5, StrongSORT, MIDAS, UniDepth |
| **Server** | FastAPI, Starlette <0.51.0, MCP 0.1+ |
| **Hardware** | pybelt, open3d |
| **Utilities** | numpy, pandas, scipy, einops, timm, wandb |

## Development

### Adding New Tests

Place test files in `tests/unit/` or `tests/integration/`:

```python
# tests/unit/test_my_component.py
import pytest
from server.my_module import MyComponent

class TestMyComponent:
    def test_initialization(self):
        component = MyComponent()
        assert component is not None
```

Run with:

```bash
python -m pytest tests/unit/test_my_component.py -v
```

### Adding New Vision Stages

Extend `VisionPipeline._inference_loop()` in `server/vision_pipeline.py`:

```python
# Add to pipeline stage sequence
outputs = self._my_new_processing_stage(outputs)
```

### Adding LLM Tools

Add to `server/auditory_interface/server_hans.py`:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("HANS-Controller")

@mcp.tool()
def my_new_tool(param: str) -> str:
    """Tool description for LLM."""
    return "Result"
```

## Performance (dependant on the exact hardware specification)

| Component | Latency | Rate |
|-----------|---------|------|
| **YOLOv5 (GPU)** | 30–50ms | 20–30 Hz |
| **StrongSORT** | 10–20ms | 50+ Hz |
| **Depth estimation** | 100ms | 3 Hz (throttled) |
| **Haptic command** | <5ms | 5 Hz (200ms BLE throttle) |
| **End-to-end** | ~200ms | ~5 Hz |

## Citation

If you use HANS in research, please cite:

```bibtex
@article{furtak2025helping,
  title={Helping blind people grasp: Enhancing a tactile bracelet with an automated hand navigation system},
  author={Furtak, M. and P{\"a}tzold, F. and Kietzmann, T. and K{\"a}rcher, S. M. and K{\"o}nig, P.},
  journal={arXiv preprint arXiv:2504.16502},
  year={2025}
}
```

## License

See `LICENSE` file for details.

## Support & Contact

For issues, questions, or suggestions:

1. Check `AGENTS.md`, `SKILLS.md`, `SOUL.md` for architectural details
2. Review test files in `tests/` for usage examples
3. Open an issue on the repository