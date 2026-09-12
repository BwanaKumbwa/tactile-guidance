# Device familiarisation (matches Experiment Protocol)

Source: **Belt–Bracelet Experiment Protocol** — sections
*Device Setup & Calibration* and *Familiarisation (~15 min, seated)*.

These scripts help the participant **feel each motor** and learn the devices.
They are not meant as a long psychophysics intensity exam.

---

## Protocol mapping

| Protocol step | What to do |
|---------------|------------|
| Fasten belt; connect BLE | Physical setup |
| Belt: feel motors / set front | Run `calibrate_belt.py` |
| Attach bracelet (see mounting notes) | Physical setup |
| Identify each bracelet + belt motor | Scripts menus “feel all / feel one” |
| Bracelet-only reach | Brief practice with system (no script) |
| Belt-only orientation (seated) | Script cardinals + brief practice |
| One full guided walk (handoff) | Normal HANS run once |

**Intensity:** start near **50%**. Change only ±5% if a motor is too weak or too strong to notice. Goal = “I can tell where that is,” not perfect equal loudness on every motor.

---

## Start server + phone

```bash
/home/amislam/miniconda3/envs/hans_android/bin/python \
  /home/amislam/tactile-guidance/hans_android/server/auditory_interface/server_main.py \
  --mode testing
```

Connect the Android app. Wait until devices link.

---

## Belt familiarisation

```bash
/home/amislam/miniconda3/envs/hans_android/bin/python \
  /home/amislam/tactile-guidance/hans_android/server/scripts/calibrate_belt.py
```

Suggested flow (~few minutes):
1. **Feel all 16 motors** — participant notices locations around the waist  
2. Note which index is under the **green marker** → **Mark as navel**  
3. **Feel the four directions** (front → right → back → left)  
4. **Flip L/R** only if those feel swapped  
5. Done  

Say something like: *“You’ll feel buzzes around the belt — just notice where each one is.”*

---

## Bracelet familiarisation

**Mounting (from protocol):**
- Control box on dominant-side upper arm, wires down  
- Bracelet two fingers above the wrist  
- Up/down motors toward the body; left/right toward the hand  
- Black felt: top = motor 2, right = motor 1  
- White felt: top motor has a red dot  

```bash
/home/amislam/miniconda3/envs/hans_android/bin/python \
  /home/amislam/tactile-guidance/hans_android/server/scripts/calibrate_bracelet.py \
  --participant 1
```

Suggested flow:
1. **Feel all 4 motors** — ask which side each buzz is on  
2. Optional **comfort check** (±5%) only if a motor is hard to feel or too strong  
3. Optionally **save** comfort levels for this participant  
4. Done  

---

## Rest of familiarisation (no extra scripts)

Still do these as in the protocol (~15 min total seated block):
1. **Bracelet-only reach** — short practice with bracelet cues alone  
2. **Belt-only orientation** — seated, turn toward belt cues  
3. **One full guided walk** through belt → bracelet handoff so they know the transition  

Then continue with **Training to criterion** (excluded from analysis).

---

## After familiarisation — quick system check

1. Bottle ~2 m, clear path  
2. Voice: “HANS help me grab the bottle”  
3. Confirm belt steers when far, stops near ~50 cm, bracelet guides the hand  

Optional: static chair mid-path for one avoidance demo (not required for familiarisation).
