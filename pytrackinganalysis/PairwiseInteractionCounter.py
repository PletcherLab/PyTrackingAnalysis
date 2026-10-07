import pandas as pd
import numpy as np 
from . import Counter
from . import PairwiseInteractionTracker
import matplotlib.pyplot as plt
import matplotlib.patches as patches

class PairwiseInteractionCounter(Counter.Counter):
    def __init__(self, tracking_region_id, tracking_regions, counting_regions, parameters, exp_design,rawdata):
        super().__init__(tracking_region_id, tracking_regions, counting_regions, parameters, exp_design,rawdata)   
        ## I think that we can just create two pseudo partner trackers, make sure we have observations for each frame for both
        ## and then pass everything to corresponding tracker functions

        object_ids = self.rawdata['ObjectID'].unique()
        if(len(object_ids)<2):
            raise ValueError(f"Insufficient data for PairwiseInteractionCounter: {tracking_region_id}. Must have at least two objects.")    
        elif(len(object_ids)>2):
            print("Too many objects for PairwiseInteractionCounter.  Only the first two will be used.")
            object_ids = object_ids[:2]
        
        self.trackers=[]
        for object_id in object_ids:
            ## Create a pseudo tracker for each object.
            tmpdata = self.rawdata[self.rawdata['ObjectID']==object_id].copy()
            tmp = PairwiseInteractionTracker.PairwiseInteractionTracker(tracking_region_id, object_id, tracking_regions, counting_regions, parameters, exp_design,tmpdata)    
            self.trackers.append(tmp)

        self.trackers[0].set_neighbor(self.trackers[1])
        self.trackers[1].set_neighbor(self.trackers[0])
      
    def summarize(self, range_minutes=(0,0)):
        tmp = Counter.Counter.summarize(self, range_minutes)
        ## Pair measures only: the pseudo-trackers have no stable fly identity
        ## from frame to frame, so their per-fly open-field measures would be
        ## meaningless (and the costliest thing in the pipeline to compute).
        tmp2 = self.trackers[0].get_pair_measures(range_minutes)
        tmp3 = self.trackers[1].get_pair_measures(range_minutes)

        mean_neighbor_distance = (tmp2['MeanDistance'] + tmp3['MeanDistance'])/2
        median_neighbor_distance = (tmp2['MedianDistance'] + tmp3['MedianDistance'])/2
        frames_interacting = [(tmp2[f"FramesInteracting_{dist}"] + tmp3[f"FramesInteracting_{dist}"])/2 for dist in self.parameters.interaction_distance_mm]
        total_valid_frames = (tmp2['ValidFrames'] + tmp3['ValidFrames'])/2
        percent_frames_interacting = [(tmp2[f"PercentInteracting_{dist}"] + tmp3[f"PercentInteracting_{dist}"])/2 for dist in self.parameters.interaction_distance_mm]
        
        distance_names = [f"FramesInteracting_{dist}" for dist in self.parameters.interaction_distance_mm]    
        distance_names2 = [f"PercentInteracting_{dist}" for dist in self.parameters.interaction_distance_mm]    
        
        frames_interacting_series = pd.Series(frames_interacting, index=distance_names)
        percent_frames_interacting_series = pd.Series(percent_frames_interacting, index=distance_names2)
        
        result = pd.concat([tmp,pd.Series({'MeanDistance': mean_neighbor_distance}),pd.Series({"MedianDistance" : median_neighbor_distance}),\
            pd.Series({'ValidFrames': total_valid_frames, 'MergedFraction': tmp2['MergedFraction']}),
            frames_interacting_series,percent_frames_interacting_series])

        return result      
    
    def plot_time_dependent_distances(self,window_size_min=10,step_size_min=5,range_minutes=(0,0)):
        self.trackers[0].plot_time_dependent_distances(window_size_min,step_size_min,range_minutes)
    
    def plot_time_dependent_interactions(self,window_size_min=10,step_size_min=5,range_minutes=(0,0)):
        self.trackers[0].plot_time_dependent_interactions(window_size_min,step_size_min,range_minutes)