import threading
from typing import Optional


class SharedState:
    """
    Thread-safe container updated by the YOLO vision thread and read
    by the FastAPI / MCP layers.  Each logical domain has its own lock
    so high-frequency vision writes (set_visible_objects at 30 Hz) do not
    block low-frequency preference reads (per voice command).
    """

    def __init__(self):
        # Hot path: vision writes every frame
        self._vis_lock        = threading.Lock()
        self._visible_objects: list = []

        # Medium path: per-command writes
        self._target_lock     = threading.Lock()
        self._current_target: str  = "none"
        self._target_list:    list = []
        self._list_mode:      str  = "ordered"

        # Medium path: world-map (per confident detection)
        self._map_lock        = threading.Lock()
        self._world_map:      dict = {}

        # Cold path: per-voice-command writes
        self._pref_lock       = threading.Lock()
        self._preferences:    dict = {"speech_speed": "normal", "verbosity": "normal"}

        # Cold path: per-connection writes
        self._hw_lock         = threading.Lock()
        self._bracelet_connected: bool = False
        self._belt_connected:     bool = False

        # Write-once (set at startup)
        self._meta_lock       = threading.Lock()
        self._available_classes: list = []
        self._memory_existed:    bool = False

    # Hot path — visible objects (~30 Hz writes from YOLO thread)

    def set_visible_objects(self, objects: list) -> None:
        with self._vis_lock:
            self._visible_objects = list(objects)

    def get_visible_objects(self) -> list:
        with self._vis_lock:
            return list(self._visible_objects)

    # Medium path — active target + target list

    def set_target(self, target: str) -> None:
        with self._target_lock:
            self._current_target = target

    def get_target(self) -> str:
        with self._target_lock:
            return self._current_target

    def set_target_list_state(self, target_list: list, mode: str) -> None:
        with self._target_lock:
            self._target_list = list(target_list)
            self._list_mode   = mode

    def get_target_list_state(self) -> dict:
        with self._target_lock:
            return {"targets": list(self._target_list), "mode": self._list_mode}

    # Medium path — world map (per confident detection)

    def update_world_map(self, new_data: dict) -> None:
        with self._map_lock:
            self._world_map.update(new_data)

    def get_world_map(self) -> dict:
        with self._map_lock:
            return dict(self._world_map)

    # Cold path — user preferences

    def set_preferences(self, prefs: dict) -> None:
        with self._pref_lock:
            self._preferences = dict(prefs)

    def get_preferences(self) -> dict:
        with self._pref_lock:
            return dict(self._preferences)

    # Cold path — hardware connection status

    def set_hardware_status(self, bracelet: bool, belt: bool) -> None:
        with self._hw_lock:
            self._bracelet_connected = bracelet
            self._belt_connected     = belt

    def get_hardware_status(self) -> dict:
        with self._hw_lock:
            return {
                "bracelet": self._bracelet_connected,
                "belt":     self._belt_connected
            }

    # Write-once — available classes (set after model load)

    def set_available_classes(self, classes: list) -> None:
        with self._meta_lock:
            self._available_classes = list(classes)

    def get_available_classes(self) -> list:
        with self._meta_lock:
            return list(self._available_classes)

    # Write-once — memory existence flag (set at boot)

    def set_memory_existed(self, existed: bool) -> None:
        with self._meta_lock:
            self._memory_existed = existed

    def get_memory_existed(self) -> bool:
        with self._meta_lock:
            return self._memory_existed

    # Convenience — full snapshot for the /internal/state endpoint

    def get_full_state(self) -> dict:
        """
        Assembles a complete state snapshot without holding multiple locks
        simultaneously (avoids deadlock risk from nested lock acquisition).
        Each field is read independently.
        """
        return {
            "target":           self.get_target(),
            "visible_objects":  self.get_visible_objects(),
            "available_classes":self.get_available_classes(),
            **self.get_target_list_state(),   # target_list, list_mode
            "world_map":        self.get_world_map(),
        }
