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
| End trial | `POST /study/trial/end` with handoff + grasp (see below) |
| Status | `GET /study/status` |

**Per-frame `frames.csv` (enriched):**
- Target box: `target_xc/yc/w/h`, `target_track_id`, `target_class_id`, `target_conf`, `target_depth_m`
- Hand box: `hand_detected`, `hand_xc/yc/w/h`, `hand_track_id`, `hand_class_id`, `hand_conf`, `hand_depth_m`
- Belt vib (last command): `belt_last_cue`, `belt_vib_angle_deg`, `belt_vib_intensity`, `belt_vib_motor_index`
- Bracelet vib (last command): `bracelet_vib_angle_deg`, `bracelet_vib_intensity`, `bracelet_vib_motor` (nearest left/right/top/bottom)

**End-trial marks (two independent fields — not combined):**
```bash
curl -s -X POST http://127.0.0.1:8000/study/trial/end \
  -H 'Content-Type: application/json' \
  -d '{"handoff_outcome":"success","grasp_outcome":"fail"}'
```
- `handoff_outcome`: participant reports the belt stopped vibrating (**thesis trial success**)  
- `grasp_outcome`: participant reports the bracelet helped them grasp (logged separately)  

Early stop: `{"end_reason":"timeout"}` / `abort` / `tech_failure`.

Training trials: set `"include_in_analysis": false` and `"block":"training"`.

Optional env (before starting server):
- `STUDY_PARTICIPANT=1`
- `STUDY_SAVE_VIDEO=1` (default) / `0` to skip overlay.mp4
- `STUDY_NOTES=pilot`

Desktop ExperimentRunner keys (standalone): `S` start, `K` sync, `Y` handoff success, `N` handoff fail, `F` tech_fail, `A` abort, `C` quit.
(For full handoff + grasp marks, prefer `run_study_session.py`.)

Still record separately: fixed side camera (floor tape), NASA-TLX after blocks, usability + debrief at session end.
