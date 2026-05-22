# HANS Skills & Tools

## Target Management

### `set_target_with_fuzzy_match(target_name: str)` [SINGLE TARGET]
**Purpose:** Set a SINGLE target with automatic speech-to-text correction
**When to use:** 
- User says "Set target to X" (singular)
- User wants to find/navigate to ONE object
- Handles "cop" → "cup" automatically
- ONLY works if target is currently visible
**Example:**
- User: "Guide me to the cup"
- → Calls `set_target_with_fuzzy_match("cup")`
- → Sets target to "cup"

### `add_targets_to_list(target_names: list, mode: str)` [MULTIPLE TARGETS]
**Purpose:** Add MULTIPLE targets to the list (even if not currently visible)
**When to use:**
- User says "Add X and Y to the target list"
- User wants to grasp multiple objects
- User wants ordered/unordered search sequence
- Targets don't need to be currently visible
**Modes:**
- `"ordered"` - Visit targets in the order specified
- `"unordered"` - Visit targets in any order (more flexible)
**Example:**
- User: "Add apple and cup to the target list in any order"
- → Calls `add_targets_to_list(["apple", "cup"], "unordered")`
- → Does fuzzy matching for visible objects
- → Adds non-visible targets to the list anyway
- → Response: "Target list set to unordered mode: ✓ cup (visible), ○ apple (not currently visible - will search when it appears)"

### `clear_target_list()` [UTILITY]
**Purpose:** Clear all targets from the list
**When to use:**
- User says "Clear targets" or "Never mind"
- User wants to start fresh

### `set_target_list(targets: list, mode: str)` [INTERNAL ONLY]
**Purpose:** Set targets directly (programmatic use only)
**When to use:** NEVER use for direct user requests - use `set_target_with_fuzzy_match()` or `add_targets_to_list()` instead

### `find_similar_target(target_name: str)` [DIAGNOSTIC]
**Purpose:** Find objects similar to a requested target
**When to use:**
- Before calling `set_target_list()` to verify the match
- To get alternative suggestions if exact match not found
- Returns: JSON with matched target, confidence score, and alternatives

### `grasp_complete()`
**Purpose:** Signal that user has successfully grasped the target
**When to use:**
- User says "I got it", "I have it", "I grasped it", "I have the cup"
- User confirms successful grasp
**Example:** User: "I got the cup" → Call `grasp_complete()` → Respond: "Great! Target cleared."

### `get_current_target_list()`
**Purpose:** Get the current target list and list mode (ordered/unordered).
**When to use:**
- User asks about what objects are currently in the target list.
- User asks what is the current target.

### `get_grasped_objects_history()`
**Purpose:** Get the list of previously grasped objects from memory.
**When to use:**
- User asks "What have I already grasped?" or "Show my progress"

## Object Detection (PRIMARY)
### `get_visible_objects()`
**Purpose:** Get list of currently visible objects from YOLO detection
**When to use (FIRST):** 
- User asks "What do you see?"
- User asks "What objects are visible?"
- User asks "What's in front of me?"
- User asks any simple detection question
- This is ALWAYS the first tool to try for object detection
**Result:** Fast, accurate list with confidence scores and distances
**Example:** 
- User: "What objects do you see?"
- → Call `get_visible_objects()` immediately
- → If no objects found, THEN fall back to `analyze_camera_view`

## Vision & Analysis (FALLBACK/DETAILED)
### `analyze_camera_view(instruction: str = "describe")`
**Purpose:** Advanced visual analysis using LLM vision capabilities
**When to use (ONLY IF):**
- `get_visible_objects()` returned no results
- User asks for complex spatial analysis
- User asks for obstacle assessment
- User asks about hand position or grasp readiness
- User asks descriptive questions like "describe the scene in detail"
- User asks about specific visual properties

**Instructions:**
- `"describe"` - Detailed scene description (fallback from get_visible_objects)
- `"obstacles"` - Check for hazards/obstacles
- `"hand_position"` - Analyze hand location and orientation

## Hardware & Settings
### `set_speech_speed(speed: str)`
**Purpose:** Change speech rate
**Speeds:** "slow" | "normal" | "fast"
**When to use:** User asks to speak faster/slower

### `set_verbosity(level: str)`
**Purpose:** Change response detail level
**Levels:** "concise" | "normal" | "verbose"
**When to use:** User asks for more/less detail

### `get_hardware_status()`
**Purpose:** Check Bluetooth connections
**When to use:** User asks "Is my bracelet connected?", hardware troubleshooting

## Hand Guidance (Vision Pipeline)
### `control_vision(instruction: str, value: str = "")`
**Purpose:** Direct vision pipeline commands
**Instructions:** "detect", "capture", "navigate", "stop"
**When to use:** Advanced hand guidance operations