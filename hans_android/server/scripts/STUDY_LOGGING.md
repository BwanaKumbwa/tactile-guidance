# Device familiarisation (matches Experiment Protocol)
#
# Use after server is running and devices are connected.
# See also study logging endpoints under /study/* for thesis data capture.

## Prerequisites

1. Start the server:
   ```bash
   /home/amislam/miniconda3/envs/hans_android/bin/python \
     /home/amislam/tactile-guidance/hans_android/server/auditory_interface/server_main.py --mode testing
   ```
2. Connect phone + belt + bracelet (AI thread must be up).

## Belt familiarisation

```bash
/home/amislam/miniconda3/envs/hans_android/bin/python \
  /home/amislam/tactile-guidance/hans_android/server/scripts/calibrate_belt.py
```

## Bracelet familiarisation

```bash
/home/amislam/miniconda3/envs/hans_android/bin/python \
  /home/amislam/tactile-guidance/hans_android/server/scripts/calibrate_bracelet.py \
  --participant 1
```

## Study trial logging (thesis)

Data lands in `hans_android/server/results/study/P{id}/{session}/`.

| Step | Call |
|------|------|
| Start / switch participant | `POST /study/session/start` `{"participant_id":1,"notes":"..."}` |
| Start trial | `POST /study/trial/start` `{"block":"direct","approach_type":"direct","distance_m":2.0}` |
| Sync at t₀ (voice parse) | `POST /study/sync` |
| End trial | `POST /study/trial/end` `{"outcome":"success"}` |
| Status | `GET /study/status` |

Outcomes: `success` | `fail` | `timeout` | `abort` | `tech_failure`.  
Training trials: set `"include_in_analysis": false` and `"block":"training"`.

Optional env (before starting server):
- `STUDY_PARTICIPANT=1`
- `STUDY_SAVE_VIDEO=1` (default) / `0` to skip overlay.mp4
- `STUDY_NOTES=pilot`

Desktop ExperimentRunner keys (standalone): `S` start, `K` sync, `Y` success, `N` fail, `F` tech_fail, `A` abort, `C` quit.

Still record separately: fixed side camera (floor tape), NASA-TLX after blocks, usability + debrief at session end.
