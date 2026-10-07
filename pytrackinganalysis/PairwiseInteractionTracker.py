import logging

import pandas as pd
import numpy as np
from . import Tracker
from . import openfield
from . import windowing
import matplotlib.pyplot as plt
import matplotlib.patches as patches

logger = logging.getLogger(__name__)


def classify_merges(distance_mm, measured, merge_distance_mm):
    """Boolean mask of Merged frames (ADR-0014).

    A maximal run of unmeasured frames is Merged when the measured distance
    just before it *and* just after it are both below *merge_distance_mm*:
    the two flies were touching going in and coming out, so the lost frames
    are the flies resolved as one blob, not a fly gone missing. A run that
    touches either end of the recording has only one bracket and stays
    unmeasured. Positional — the inputs must be in frame order.
    """
    distance = pd.Series(np.asarray(distance_mm, dtype=float))
    measured = pd.Series(np.asarray(measured, dtype=bool))
    if merge_distance_mm is None or merge_distance_mm <= 0 or measured.all():
        return np.zeros(len(measured), dtype=bool)
    known = distance.where(measured)
    before = known.ffill().shift(1)       # last measured distance strictly before
    after = known.bfill().shift(-1)       # first measured distance strictly after
    lost = ~measured
    run = (lost != lost.shift()).cumsum()
    ## A run's brackets are what its first frame saw before and its last frame
    ## saw after; NaN (no bracket) compares False, so edge runs never merge.
    entering = before.groupby(run).transform('first')
    leaving = after.groupby(run).transform('last')
    merged = lost & (entering < merge_distance_mm) & (leaving < merge_distance_mm)
    return merged.to_numpy()


def encounter_bouts(distance_mm, valid, minutes, d, hysteresis_mm, gap_s, min_s):
    """Encounters below *d* as ``(start, end)`` positional index pairs (ADR-0014).

    *distance_mm* already carries 0 on merged frames and *valid* includes
    them. An Encounter starts below *d* and lasts until the distance rises
    above ``d + hysteresis_mm`` — so a pair hovering at the threshold is one
    Encounter, not dozens. Lost frames are breaks like any other, bridged
    when shorter than *gap_s*; Encounters shorter than *min_s* are dropped.
    """
    distance = np.asarray(distance_mm, dtype=float)
    valid = np.asarray(valid, dtype=bool)
    state = np.full(distance.shape, np.nan)
    state[valid & (distance < d)] = 1.0
    state[valid & (distance > d + hysteresis_mm)] = 0.0
    ## Between the thresholds a valid frame keeps the last valid state; lost
    ## frames are NaN here too, so the fill carries across them.
    held = pd.Series(state).ffill().fillna(0.0).to_numpy()
    close = valid & (held == 1.0)
    return openfield.find_bouts(close, minutes, bridge_s=gap_s, min_s=min_s)


class PairwiseInteractionTracker(Tracker.Tracker):
    def __init__(self, tracking_region_id, object_id, tracking_regions, counting_regions, parameters, exp_design,rawdata):
        ## All of the relevant parameters are defined in the parent class.
        super().__init__(tracking_region_id, object_id, tracking_regions, counting_regions, parameters, exp_design,rawdata)     
        if(self.rawdata.index.duplicated().any()):                 
            raise ValueError(f"Duplicated frames in {self.name}.")
        
    def set_neighbor(self,neighbor_tracker):
        self.neighbor_tracker = neighbor_tracker

        if 'ClosestNeighbor' not in self.rawdata.columns:
            self.set_neighbor_distance()
        if 'ClosestNeighbor' not in neighbor_tracker.rawdata.columns:
            ## The validity mask reads both sides of the pair, so the partner needs
            ## its distance column too — otherwise whichever tracker is paired first
            ## raises KeyError on data that DTrack did not pre-compute.
            neighbor_tracker.neighbor_tracker = self
            neighbor_tracker.set_neighbor_distance()

        self.set_neighbor_quality_and_distance_mm()
        self.update_neighbor_interactions()
           
    def set_neighbor_distance(self):
        ## Check to make sure the short cut will work.
        ## It will only if each row of each tracker has the same frame number.
        ## This might not actually be needed because vector operations are done based on 
        ## index, which I set to frame during initialization.
        #if(sum(self.rawdata['Frame'] - self.neighbor_tracker.rawdata['Frame'])!=0):
        #    raise ValueError(f"Frame mismatch between trackers.")
    
        ## This is the distance between the two trackers at the current time point.
        x1 = self.rawdata['X']
        y1 = self.rawdata['Y']
        x2 = self.neighbor_tracker.rawdata['X']
        y2 = self.neighbor_tracker.rawdata['Y']
        distance = np.sqrt((x1-x2)**2+(y1-y2)**2) 
        self.rawdata['ClosestNeighbor']=distance

    ## This function is called in Arena post processing because it needs access
    ## to other trackers.       
    def set_neighbor_quality_and_distance_mm(self):
        ## Flags the frames whose neighbour distance can be trusted.
        ## DataQuality values are High, Low, Indiscernible, and NotFound.
        ##
        ## Both trackers must be High AND the pair must account for exactly two
        ## blobs AND the distance must be a real measurement. The quality and
        ## blob-count conditions were previously OR'd, which admitted frames
        ## where one animal was NotFound but the object counts happened to sum
        ## to two — a lost animal is not a valid distance observation.
        quality = (self.rawdata['DataQuality']=="High") & (self.neighbor_tracker.rawdata['DataQuality']=="High")
        two_objects = (self.rawdata['NObjects'] + self.neighbor_tracker.rawdata['NObjects']) == 2
        neg_one_distance = (self.rawdata['ClosestNeighbor'] == -1) | (self.neighbor_tracker.rawdata['ClosestNeighbor'] == -1)
        null_observations = (self.rawdata['ClosestNeighbor'].isnull()) | (self.neighbor_tracker.rawdata['ClosestNeighbor'].isnull())

        measured = quality & two_objects & (~neg_one_distance) & (~null_observations)
        ## The masks above combine two frames by index; a frame only one side
        ## recorded comes back NaN, which is not a measurement.
        measured = measured.reindex(self.rawdata.index, fill_value=False).astype(bool)
        self.rawdata['IsNeighborMeasured'] = measured

        distance_mm = self.rawdata['ClosestNeighbor'].where(measured) * self.parameters.mm_per_pixel

        ## Frames where the flies touched and DTrack lost one of them are
        ## contact, not missing data (ADR-0014): valid, at distance 0. Every
        ## other lost frame stays out of both numerator and denominator.
        merged = pd.Series(
            classify_merges(distance_mm, measured,
                            getattr(self.parameters, 'merge_distance_mm', 0)),
            index=self.rawdata.index)
        self.rawdata['IsMerged'] = merged
        self.rawdata['IsNeighborValid'] = measured | merged
        self.rawdata['ClosestNeighbor_mm'] = distance_mm.mask(merged, 0.0)

    def get_interaction_subset(self, range_minutes):
        """Interaction rows within ``[start, end)``; ``(0, 0)`` means the whole recording."""
        return windowing.slice_by_minutes(self.interaction_data, range_minutes)

    def get_frames_interacting(self, range_minutes=(0,0)):
        data = self.get_interaction_subset(range_minutes)
        results =[]
        for dist in self.parameters.interaction_distance_mm:
            results.append(data[f'Interaction_{dist}'].sum()) 
        return results
    
    def get_percent_frames_interacting(self, range_minutes=(0,0)):
        data = self.get_interaction_subset(range_minutes)
        results =[]
        for dist in self.parameters.interaction_distance_mm:
            results.append(data[f'Interaction_{dist}'].sum()) 
        total_valid_frames = self.get_total_frames_with_valid_neighbor(range_minutes)
        if(total_valid_frames==0):
            ## Nothing measured is no measurement, not "never interacted".
            perc_results = [np.nan]*len(results)
        else:
            perc_results=[x/total_valid_frames for x in results]
        return perc_results
    def get_total_frames_with_valid_neighbor(self, range_minutes=(0,0)):
        data = self.get_data_subset(range_minutes)
        return data['IsNeighborValid'].sum()

    def get_mean_neighbor_distance(self, range_minutes=(0,0)):
        data = self.get_data_subset(range_minutes)
        return data['ClosestNeighbor_mm'].mean()

    def get_median_neighbor_distance(self, range_minutes=(0,0)):
        data = self.get_data_subset(range_minutes)
        return data['ClosestNeighbor_mm'].median()

    def is_partner(self, tracker):
        return self.tracking_region_id == tracker.tracking_region_id
    
    def update_neighbor_interactions(self):
        self.interaction_data = self.rawdata.loc[:,['Minutes','Indicator','ClosestNeighbor_mm','IsNeighborValid','IsMerged']]
        for dist in self.parameters.interaction_distance_mm:
            self.interaction_data[f'Interaction_{dist}'] = (self.interaction_data['ClosestNeighbor_mm'] < dist) & (self.interaction_data['IsNeighborValid'])    
      
    def summarize(self, range_minutes=(0,0)):
        """One fly's row: the Tracker base, the fly's open-field measures
        (ADR-0015), and its Pair's proximity measures (ADR-0014), which both
        flies of the Pair share. ``Arena`` collapses the two rows (ADR-0013)."""
        return pd.concat([Tracker.Tracker.summarize(self, range_minutes),
                          self.get_open_field_measures(range_minutes),
                          self.get_pair_measures(range_minutes)])

    # ---- open-field measures (ADR-0015) -------------------------------

    def arena_geometry(self):
        """This region's arena as drawn, or None when its ROI cannot describe one."""
        if not hasattr(self, '_arena_geometry'):
            try:
                self._arena_geometry = openfield.ArenaGeometry.from_roi(
                    self.tracking_region_roi, self.parameters.mm_per_pixel)
            except (KeyError, ValueError, TypeError) as err:
                logger.warning("No arena geometry for %s (%s); open-field measures "
                               "will be NA.", self.name, err)
                self._arena_geometry = None
        return self._arena_geometry

    def _ensure_heading(self):
        """Body-axis estimate over the whole recording, computed once: the
        smoothing window looks either side of each frame, so it must see past
        a phase boundary rather than be recomputed inside each window."""
        if 'Heading' not in self.rawdata.columns:
            self.rawdata['Heading'] = openfield.smoothed_heading(
                self.rawdata['Xpos_mm'].to_numpy(), self.rawdata['Ypos_mm'].to_numpy(),
                (self.rawdata['DataQuality'] == 'High').to_numpy(),
                self.rawdata['Minutes'].to_numpy())

    def get_open_field_measures(self, range_minutes=(0,0)):
        """Centrophobism, exploration and walking structure for this fly."""
        self._ensure_heading()
        return openfield.fly_measures(self.get_data_subset(range_minutes),
                                      self.arena_geometry(), self.parameters)

    def get_coverage_curve(self, range_minutes=(0,0), n_points=60):
        """``(elapsed minutes, share of arena covered)`` across a window, or
        ``None`` without geometry or frames — the curve behind ExplorationAUC."""
        geometry = self.arena_geometry()
        if geometry is None:
            return None
        self._ensure_heading()
        data = self.get_data_subset(range_minutes)
        if len(data) < 2:
            return None
        minutes = data['Minutes'].to_numpy(dtype=float)
        first = openfield.footprint_first_visits(
            data['Xpos_mm'].to_numpy(), data['Ypos_mm'].to_numpy(),
            (data['DataQuality'] == 'High').to_numpy(), minutes, geometry,
            data['Heading'].to_numpy(), length_mm=self.parameters.fly_length_mm,
            width_mm=self.parameters.fly_width_mm)
        elapsed = np.linspace(0.0, minutes[-1] - minutes[0], n_points)
        return elapsed, openfield.coverage_curve(first, minutes[0] + elapsed)

    # ---- pair proximity measures (ADR-0014) ---------------------------

    def get_pair_measures(self, range_minutes=(0,0)):
        """The Pair's proximity measures, identical from either fly's side.

        Interaction is a fraction of *valid* frames — measured or merged —
        and an Encounter rate is per valid minute, so tracking losses shrink
        the denominator rather than read as time apart.
        """
        distances = self.parameters.interaction_distance_mm
        data = self.get_interaction_subset(range_minutes)
        n_frames = len(data)
        valid = data['IsNeighborValid'].to_numpy(dtype=bool)
        n_valid = int(valid.sum())
        distance = data['ClosestNeighbor_mm']
        row = {
            'MeanDistance': distance.mean(),
            'MedianDistance': distance.median(),
            'ValidFrames': n_valid,
            'ValidFraction': n_valid / n_frames if n_frames else pd.NA,
            'MergedFraction': int(data['IsMerged'].sum()) / n_valid if n_valid else pd.NA,
        }
        frames = {d: int(data[f'Interaction_{d}'].sum()) for d in distances}
        for d in distances:
            row[f'FramesInteracting_{d}'] = frames[d]
        ## No valid frame is no measurement: NA, never 0 — a phase the
        ## recording does not reach used to read as "never interacted" and was
        ## tested as such.
        for d in distances:
            row[f'PercentInteracting_{d}'] = frames[d] / n_valid if n_valid else pd.NA

        minutes = data['Minutes'].to_numpy(dtype=float)
        observed = float(minutes[-1] - minutes[0]) if n_frames else 0.0
        valid_minutes = observed * n_valid / n_frames if n_frames else 0.0
        encounters = {}
        for d in distances:
            bouts = encounter_bouts(
                distance.to_numpy(dtype=float), valid, minutes, d,
                self.parameters.encounter_hysteresis_mm,
                self.parameters.encounter_gap_s, self.parameters.encounter_min_s)
            encounters[d] = openfield.bout_summary(bouts, minutes, valid_minutes)
        for name, i in (('EncounterRate', 0), ('MeanEncounterDuration', 1),
                        ('LatencyToFirstEncounter', 2)):
            for d in distances:
                value = encounters[d][i]
                row[f'{name}_{d}'] = pd.NA if pd.isna(value) else value
        return pd.Series(row)
    
    def get_time_dependent_interactions(self,window_size_min=10,step_size_min=5,range_minutes=(0,0)):
        data_subset = self.get_interaction_subset(range_minutes)
        columns = ['StartMin','EndMin'] + [f'PercentInteractions_{dist}' for dist in self.parameters.interaction_distance_mm]
        if(len(data_subset)==0):
            return pd.DataFrame(columns=columns)
        earliest_min = round(data_subset['Minutes'].iat[0])+window_size_min
        latest_min = round(data_subset['Minutes'].iat[-1])
        interactions =[]

        for end in range(earliest_min, latest_min + 1, step_size_min):
            start = end - window_size_min
            tmp = [start,end,self.get_percent_frames_interacting([start,end])]
            tmp2=[item for sublist in tmp for item in (sublist if isinstance(sublist, list) else [sublist])]
            interactions.append(tmp2)
            
        result = [f'PercentInteractions_{dist}' for dist in self.parameters.interaction_distance_mm]
        result.insert(0,'EndMin')
        result.insert(0,'StartMin')
        return pd.DataFrame(interactions, columns=result)
    
    def get_time_dependent_distances(self,window_size_min=10,step_size_min=5,range_minutes=(0,0)):
        data_subset = self.get_data_subset(range_minutes)
        if(len(data_subset)==0):
            return pd.DataFrame(columns=['StartMin','EndMin','MeanDistance','MedianDistance'])
        earliest_min = round(data_subset['Minutes'].iat[0])+window_size_min
        latest_min = round(data_subset['Minutes'].iat[-1])        
        results =[]
        for end in range(earliest_min, latest_min + 1, step_size_min):
            start = end - window_size_min
            tmp = self.get_data_subset([start,end])
            results.append([start,end,tmp['ClosestNeighbor_mm'].mean(),tmp['ClosestNeighbor_mm'].median()])
        return pd.DataFrame(results, columns=['StartMin','EndMin','MeanDistance','MedianDistance'])
    
    def plot_time_dependent_distances(self,window_size_min=10,step_size_min=5,range_minutes=(0,0)):
        data = self.get_time_dependent_distances(window_size_min,step_size_min,range_minutes)
        plt.figure(figsize=(10, 6))
      
        for column in data.columns:
            if column not in ['StartMin', 'EndMin']:
                plt.plot(data['EndMin'], data[column], marker='o', linestyle='-', label=column)
      
        plt.xlabel('Minutes')
        plt.ylabel('Neighbor Distances (mm)')
        plt.title(f'{self.name} ({self.tracking_region_design["Treatment"].iat[0]})')
        plt.legend()      
        plt.grid(True)
        plt.show()
    
    def plot_time_dependent_interactions(self,window_size_min=10,step_size_min=5,range_minutes=(0,0)):
        data = self.get_time_dependent_interactions(window_size_min,step_size_min,range_minutes)
        plt.figure(figsize=(10, 6))
      
        for column in data.columns:
            if column not in ['StartMin', 'EndMin']:
                plt.plot(data['EndMin'], data[column], marker='o', linestyle='-', label=column)
      
        plt.xlabel('Minutes')
        plt.ylabel('Fraction Time Interacting')
        plt.title(f'{self.name} ({self.tracking_region_design["Treatment"].iat[0]})')
        plt.legend()      
        plt.ylim([-0.1,1])
        plt.grid(True)
        plt.show()


    def plot_xy(self, range_minutes=(0,0)):
        """
        Plot the XY positions of the tracked object.

        Parameters:
        range_minutes (tuple): Tuple of two integers specifying the start and end minutes.
        """
        data_subset = self.get_data_subset(range_minutes)
        plt.figure(figsize=(10, 6))
        scatter = plt.scatter(data_subset['Xpos_mm']*(int)(self.tracking_region_design['XLocationMultiplier'].iloc[0]), data_subset['Ypos_mm']*(int)(self.tracking_region_design['YLocationMultiplier'].iloc[0]), c=data_subset['ClosestNeighbor_mm'], cmap='viridis', vmin=data_subset['ClosestNeighbor_mm'].min(), vmax=data_subset['ClosestNeighbor_mm'].max())
        plt.colorbar(scatter, label='Closest Neighbor')
        plt.xlabel('X Position (mm)')
        plt.ylabel('Y Position (mm)')
        title = f'{self.name} ({self.tracking_region_design["Treatment"].iloc[0]})'
        if((int)(self.tracking_region_design['YLocationMultiplier'].iloc[0])==-1 and (int)(self.tracking_region_design['XLocationMultiplier'].iloc[0])==-1):
                title = title + " (X and Y Coordinates Flipped)"
        elif((int)(self.tracking_region_design['YLocationMultiplier'].iloc[0])==-1):
                title = title + " (Y Coordinate Flipped)"
        elif((int)(self.tracking_region_design['XLocationMultiplier'].iloc[0])==-1):
                title = title + " (X Coordinate Flipped)"
        plt.title(title)
        tmp = self.get_plot_limits()
        plt.xlim(tmp[0])
        plt.ylim(tmp[1])
        plt.grid(True)

        if(self.tracking_region_roi['Shape'].values[0]=='Ellipse'):
            ellipse = patches.Ellipse((0, 0), width=self.tracking_region_roi['Width'].values[0]*self.parameters.mm_per_pixel, height=self.tracking_region_roi['Height'].values[0]*self.parameters.mm_per_pixel, edgecolor='gray', facecolor='none', linewidth=1)            
            plt.gca().add_patch(ellipse)

        plt.show()
