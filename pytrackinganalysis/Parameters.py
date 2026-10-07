from enum import Enum
import pandas as pd

class TrackingType(Enum):
    TRACKER = 1
    TWOCHOICETRACKER = 2
    XCHOICETRACKER = 3
    DDROPTRACKER = 4
    PAIRWISEINTERACTIONTRACKER = 5
    CENTROPHOBISMTRACKER = 6
    COUNTER = 7
    TWOCHOICECOUNTER = 8
    PAIRWISEINTERACTIONCOUNTER = 9

class TrackingClass(Enum):
    TRACKING = 1
    COUNTING = 2

class TrackingTypeDetails:
    def __init__(self, tracking_type=TrackingType.TRACKER):
        self.tracking_type = tracking_type
        if tracking_type in (
            TrackingType.TRACKER, TrackingType.TWOCHOICETRACKER,
            TrackingType.XCHOICETRACKER, TrackingType.DDROPTRACKER,
            TrackingType.PAIRWISEINTERACTIONTRACKER, TrackingType.CENTROPHOBISMTRACKER,
        ):
            self.tracking_class = TrackingClass.TRACKING
        elif tracking_type in (
            TrackingType.COUNTER, TrackingType.TWOCHOICECOUNTER,
            TrackingType.PAIRWISEINTERACTIONCOUNTER,
        ):
            self.tracking_class = TrackingClass.COUNTING
        else:
            raise ValueError(f"Invalid tracking type: {tracking_type}. Must be an instance of TrackingType enum.")

    def get_tracking_type(self):
        return self.tracking_type

    def get_tracking_class(self):
        return self.tracking_class


## Single source of truth for rig calibration. The pairwise-specific setters used
## to carry their own copy of these numbers, and their colosseum value (0.1106)
## disagreed with the generic one (0.108) — a 2.4% difference in every distance
## depending on which entry point the caller happened to use.
RIG_MM_PER_PIXEL = {
    'small_arena': 0.056,
    'arena_max': 0.145,
    'colosseum': 0.108,
    'obscura': 0.131,
}

## Every rig preset reads frame timing from the DTrack MSec column.
RIG_FPS = 0


class Parameters:
    def __init__(self, tracking_type=TrackingType.TRACKER, fps=0, mm_per_pixel=0.1,
                 speed_window_seconds=1, micromove_speed_mm_sec=None,
                 walking_speed_mm_sec=2, sleep_threshold_min=5):
        if micromove_speed_mm_sec is None:
            micromove_speed_mm_sec = [0.2, 2]
        self.fps = fps
        self.mm_per_pixel = mm_per_pixel
        self.speed_window_seconds = speed_window_seconds
        self.micro_move_speed_mm_sec = micromove_speed_mm_sec
        self.walking_speed_mm_sec = walking_speed_mm_sec
        self.sleep_threshold_min = sleep_threshold_min
        self.tracking_details = TrackingTypeDetails(tracking_type)
        self.interaction_distance_mm = [8]
        self.set_pairwise_defaults()

    def set_pairwise_defaults(self):
        """Pair-proximity and open-field settings (ADR-0014, ADR-0015).

        Separate from the rig presets: these describe the flies and the
        measures, not the camera, so no rig changes them.
        """
        ## A lost run bracketed by distances below this is two flies touching.
        self.merge_distance_mm = 3.0
        ## An Encounter ends only this far above its threshold, survives breaks
        ## shorter than encounter_gap_s, and must last encounter_min_s.
        self.encounter_hysteresis_mm = 0.5
        self.encounter_gap_s = 0.5
        self.encounter_min_s = 0.5
        ## Absolute wall-contact ring, and the body ellipse used for exploration.
        self.wall_zone_mm = 2.5
        self.fly_length_mm = 2.5
        self.fly_width_mm = 1.0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _set_rig_values(self, fps, mm_per_pixel, tracking_type):
        """Apply a rig calibration preset and set the tracking type."""
        self.fps = fps
        self.mm_per_pixel = mm_per_pixel
        self.speed_window_seconds = 1
        self.micro_move_speed_mm_sec = [0.2, 2]
        self.walking_speed_mm_sec = 2
        self.sleep_threshold_min = 5
        self.set_tracking_type(tracking_type)

    def _set_pairwise_rig_values(self, fps, mm_per_pixel, tracking_type, interaction_distances):
        """Apply a rig preset for pairwise interaction tracking, including distance thresholds."""
        self._set_rig_values(fps, mm_per_pixel, tracking_type)
        if isinstance(interaction_distances, list):
            self.interaction_distance_mm = interaction_distances
        else:
            raise ValueError(
                f"Invalid interaction_distances: {interaction_distances}. Must be a list."
            )

    # ------------------------------------------------------------------
    # Type / class accessors
    # ------------------------------------------------------------------

    def set_tracking_type(self, tracking_type):
        self.tracking_details = TrackingTypeDetails(tracking_type)

    def get_tracking_type(self):
        return self.tracking_details.get_tracking_type()

    def get_tracking_class(self):
        return self.tracking_details.get_tracking_class()

    # ------------------------------------------------------------------
    # Rig presets  (standard tracking)
    # ------------------------------------------------------------------

    def set_rig(self, rig_name, tracking_type):
        """Apply the calibration for a named rig from :data:`RIG_MM_PER_PIXEL`."""
        if rig_name not in RIG_MM_PER_PIXEL:
            raise ValueError(
                f"Unknown rig '{rig_name}'. Known rigs: {sorted(RIG_MM_PER_PIXEL)}."
            )
        self._set_rig_values(
            fps=RIG_FPS,
            mm_per_pixel=RIG_MM_PER_PIXEL[rig_name],
            tracking_type=tracking_type,
        )

    def set_small_arena_values(self, tracking_type):
        self.set_rig('small_arena', tracking_type)

    def set_arena_max_values(self, tracking_type):
        self.set_rig('arena_max', tracking_type)

    def set_colloseum_values(self, tracking_type):
        self.set_rig('colosseum', tracking_type)

    def set_obscura_values(self, tracking_type):
        self.set_rig('obscura', tracking_type)

    def set_movie_values(self, tracking_type, fps, mm_per_pixel):
        self._set_rig_values(fps=fps, mm_per_pixel=mm_per_pixel, tracking_type=tracking_type)

    # ------------------------------------------------------------------
    # Rig presets  (pairwise interaction tracking)
    # ------------------------------------------------------------------

    def set_pairwise_rig(self, rig_name, tracking_type, interaction_distances):
        """Apply a rig calibration plus the interaction-distance thresholds."""
        if rig_name not in RIG_MM_PER_PIXEL:
            raise ValueError(
                f"Unknown rig '{rig_name}'. Known rigs: {sorted(RIG_MM_PER_PIXEL)}."
            )
        self._set_pairwise_rig_values(
            fps=RIG_FPS,
            mm_per_pixel=RIG_MM_PER_PIXEL[rig_name],
            tracking_type=tracking_type,
            interaction_distances=interaction_distances,
        )

    def set_pairwise_interaction_values_small_arena(self, interaction_distances):
        self.set_pairwise_rig('small_arena', TrackingType.PAIRWISEINTERACTIONTRACKER, interaction_distances)

    def set_pairwise_interaction_values_arena_max(self, interaction_distances):
        self.set_pairwise_rig('arena_max', TrackingType.PAIRWISEINTERACTIONTRACKER, interaction_distances)

    def set_pairwise_interaction_values_colloseum(self, interaction_distances):
        self.set_pairwise_rig('colosseum', TrackingType.PAIRWISEINTERACTIONTRACKER, interaction_distances)

    def set_pairwise_interactioncounter_values_small_arena(self, interaction_distances):
        self.set_pairwise_rig('small_arena', TrackingType.PAIRWISEINTERACTIONCOUNTER, interaction_distances)

    def set_pairwise_interactioncounter_values_arena_max(self, interaction_distances):
        self.set_pairwise_rig('arena_max', TrackingType.PAIRWISEINTERACTIONCOUNTER, interaction_distances)

    def set_pairwise_interactioncounter_values_colloseum(self, interaction_distances):
        self.set_pairwise_rig('colosseum', TrackingType.PAIRWISEINTERACTIONCOUNTER, interaction_distances)

    # ------------------------------------------------------------------
    # Generic setter / utilities
    # ------------------------------------------------------------------

    def set(self, tracking_type=None, fps=None, mm_per_pixel=None,
            speed_window_seconds=None, micromove_speed_mm_sec=None,
            walking_speed_mm_sec=None, sleep_threshold_min=None,
            interaction_distances=None, merge_distance_mm=None,
            encounter_hysteresis_mm=None, encounter_gap_s=None,
            encounter_min_s=None, wall_zone_mm=None, fly_length_mm=None,
            fly_width_mm=None):
        for name, value in (('merge_distance_mm', merge_distance_mm),
                            ('encounter_hysteresis_mm', encounter_hysteresis_mm),
                            ('encounter_gap_s', encounter_gap_s),
                            ('encounter_min_s', encounter_min_s),
                            ('wall_zone_mm', wall_zone_mm),
                            ('fly_length_mm', fly_length_mm),
                            ('fly_width_mm', fly_width_mm)):
            if value is not None:
                setattr(self, name, float(value))
        if tracking_type is not None:
            self.set_tracking_type(tracking_type)
        if fps is not None:
            self.fps = fps
        if mm_per_pixel is not None:
            self.mm_per_pixel = mm_per_pixel
        if speed_window_seconds is not None:
            self.speed_window_seconds = speed_window_seconds
        if micromove_speed_mm_sec is not None:
            self.micro_move_speed_mm_sec = micromove_speed_mm_sec
        if walking_speed_mm_sec is not None:
            self.walking_speed_mm_sec = walking_speed_mm_sec
        if sleep_threshold_min is not None:
            self.sleep_threshold_min = sleep_threshold_min
        if interaction_distances is not None:
            if isinstance(interaction_distances, list):
                self.interaction_distance_mm = interaction_distances
            else:
                raise ValueError(
                    f"Invalid interaction_distances: {interaction_distances}. Must be a list."
                )

    def print(self):
        print(self.__str__())

    def __str__(self):
        return (
            f"tracking_type: {self.tracking_details.get_tracking_type()}\n"
            f"fps: {self.fps}\n"
            f"mm_per_pixel: {self.mm_per_pixel}\n"
            f"speed_window_seconds: {self.speed_window_seconds}\n"
            f"micromove_speed_mm_sec: {self.micro_move_speed_mm_sec}\n"
            f"walking_speed_mm_sec: {self.walking_speed_mm_sec}\n"
            f"sleep_threshold_min: {self.sleep_threshold_min}\n"
            f"interaction_distances: {self.interaction_distance_mm}\n"
            f"merge_distance_mm: {self.merge_distance_mm}\n"
            f"encounter_hysteresis_mm: {self.encounter_hysteresis_mm}\n"
            f"encounter_gap_s: {self.encounter_gap_s}\n"
            f"encounter_min_s: {self.encounter_min_s}\n"
            f"wall_zone_mm: {self.wall_zone_mm}\n"
            f"fly_length_mm: {self.fly_length_mm}\n"
            f"fly_width_mm: {self.fly_width_mm}"
        )


if __name__ == "__main__":
    p = Parameters()
    p.set_tracking_type(TrackingType.TWOCHOICECOUNTER)
    print(p.get_tracking_class())
    print(p.get_tracking_type())
