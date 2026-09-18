import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.animation import FuncAnimation
import time

from . import clock_repair
from . import windowing

class Counter:
    def __init__(self, tracking_region_id, tracking_regions, counting_regions, parameters, exp_design,rawdata):
        ## These are the key identifiers for the tracker.  They are defined by the data file.
        self.tracking_region_id = tracking_region_id
        ## Contact the two identifying features into a unique name for the tracking blob.
        self.name = f'{tracking_region_id}'
        self.rawdata = rawdata        
        ## Unlike trackers, we can't index by frame because there are likely several observations per frame, one for each blob.
        self.rawdata.reset_index(drop=True, inplace=True)
        self.parameters =parameters        
        #print(tracking_region_id)
        ## Now to get the information from the experiment design file (new format).  This is used in the summary function and 
        ## will likely be used in all stats like functions that combine replicates within a treatment group.        
        if(exp_design is not None):                              
            self.tracking_region_design = exp_design.get_tracking_region(self.tracking_region_id)
            self.counting_regions_design = exp_design.counting_regions                     
        else:
            self.tracking_region_design = None
            self.counting_regions_design = None
        
        ## These are the ROI from the experiment file.  They are used for plotting and presumably for centrophobism stuff.
        self.tracking_region_roi = tracking_regions[tracking_regions['Name']==tracking_region_id].reset_index(drop=True)        
        self.counting_regions_roi = counting_regions

        ## These functions should come after all the parameters are set.        
        self.calculate_minutes()
        self.rawdata['Xpos_mm'] = self.rawdata['RelX']*self.parameters.mm_per_pixel
        self.rawdata['Ypos_mm'] = self.rawdata['RelY']*self.parameters.mm_per_pixel

    def calculate_minutes(self):
        if(self.parameters.fps==-1):
            fulltime = pd.to_datetime(self.rawdata['Time'])+pd.to_timedelta(self.rawdata['Millisec'],unit='ms')
            timediff = fulltime.diff().dt.total_seconds().copy()
            timediff.iat[0]=0
            self.rawdata['Minutes']= timediff.cumsum()/60
        elif (self.parameters.fps==0):
            self.rawdata['Minutes'] = self.rawdata['MSec']/(1000*60)
        elif (self.parameters.fps>0):
            ## If there is a defined FPS we will use it directly with the frame number.
            ## Can't trust the MSec column so we will base time on frames.
            self.rawdata['Minutes'] = self.rawdata['Frame']/(self.parameters.fps*60)
        else:
            raise ValueError(
                f"Invalid fps {self.parameters.fps} for counter {self.name}. "
                "Use -1 (wall-clock timestamps), 0 (DTrack MSec column), or a positive frame rate."
            )
        ## Same guard as Tracker: a clock set back mid-run must be repaired on
        ## disk, not analysed. Several blobs share a frame here, so consecutive
        ## rows repeat a stamp; only a genuine backward step trips the check.
        clock_repair.raise_if_rolled_back(
            self.name, self.rawdata['Frame'], self.rawdata['Minutes'] * 60_000)
        return
    
    def get_plot_limits(self):
        xlims=(self.tracking_region_roi['Width'].values[0]*self.parameters.mm_per_pixel)/(-2.0),(self.tracking_region_roi['Width'].values[0]*self.parameters.mm_per_pixel)/(2.0)
        ylims=(self.tracking_region_roi['Height'].values[0]*self.parameters.mm_per_pixel)/(-2.0),(self.tracking_region_roi['Height'].values[0]*self.parameters.mm_per_pixel)/(2.0)
        return xlims,ylims
        
    def get_data_subset(self, range_minutes):
        """Rows within ``[start, end)`` minutes; ``(0, 0)`` means the whole recording."""
        return windowing.slice_by_minutes(self.rawdata, range_minutes)

    def get_y_positions(self, range_minutes=(0,0)):
        data_subset = self.get_data_subset(range_minutes)        
        return data_subset['Ypos_mm']*(int)(self.tracking_region_design['YLocationMultiplier'].iloc[0])
    def get_x_positions(self, range_minutes=(0,0)):
        data_subset = self.get_data_subset(range_minutes)        
        return data_subset['Xpos_mm']*(int)(self.tracking_region_design['XLocationMultiplier'].iloc[0])

    def plot_xy(self, range_minutes=(0,0)):
        data_subset = self.get_data_subset(range_minutes)
        plt.figure(figsize=(10, 6))
        scatter = plt.scatter(data_subset['Xpos_mm']*(int)(self.tracking_region_design['XLocationMultiplier'].iat[0]), data_subset['Ypos_mm']*(int)(self.tracking_region_design['YLocationMultiplier'].iat[0]), c=data_subset['Minutes'], cmap='viridis', vmin=data_subset['Minutes'].min(), vmax=data_subset['Minutes'].max())
        plt.colorbar(scatter, label='Minutes')
        plt.xlabel('X Position (mm)')
        plt.ylabel('Y Position (mm)')
        title = f'{self.name} ({self.tracking_region_design["Treatment"].iat[0]})'
        if((int)(self.tracking_region_design['YLocationMultiplier'].iat[0])==-1 and (int)(self.tracking_region_design['XLocationMultiplier'].iat[0])==-1):
                title = title + " (X and Y Coordinates Fipped)"
        elif((int)(self.tracking_region_design['YLocationMultiplier'].iat[0])==-1):
                title = title + " (Y Coordinate Fipped)"
        elif((int)(self.tracking_region_design['XLocationMultiplier'].iat[0])==-1):
                title = title + " (X Coordinate Fipped)"
        plt.title(title)
        tmp = self.get_plot_limits()
        plt.xlim(tmp[0])
        plt.ylim(tmp[1])
        plt.grid(True)

        if(self.tracking_region_roi['Shape'].values[0]=='Ellipse'):
            ellipse = patches.Ellipse((0, 0), width=self.tracking_region_roi['Width'].values[0]*self.parameters.mm_per_pixel, height=self.tracking_region_roi['Height'].values[0]*self.parameters.mm_per_pixel, edgecolor='gray', facecolor='none', linewidth=1)            
            plt.gca().add_patch(ellipse)

        plt.show()
        
    def plot_x(self, bins=30, range_minutes=(0, 0)):
        data_subset = self.get_data_subset(range_minutes)
        
        plt.figure(figsize=(10, 6))
        plt.hist(data_subset['Xpos_mm']*(int)(self.tracking_region_design['XLocationMultiplier'].iloc[0]), bins=bins, edgecolor='black')
        plt.xlabel('X Position (mm)')
        plt.ylabel('Frequency')
        title = f'{self.name} ({self.tracking_region_design["Treatment"].iat[0]})'
        if((int)(self.tracking_region_design['XLocationMultiplier'].iat[0])==-1):
                title = title + " (X Coordinate Fipped)"
        plt.title(title)
        tmp = self.get_plot_limits()
        plt.xlim(tmp[0])
        plt.grid(True)
        plt.show()
        
    def plot_y(self, bins=30,range_minutes=(0, 0)):
        
        data_subset = self.get_data_subset(range_minutes)
        
        plt.figure(figsize=(10, 6))
        plt.hist(data_subset['Ypos_mm']*(int)(self.tracking_region_design['YLocationMultiplier'].iat[0]), bins=bins, edgecolor='black')
        plt.xlabel('Y Position (mm)')
        plt.ylabel('Frequency')
        title = f'{self.name} ({self.tracking_region_design["Treatment"].iat[0]})'
        if((int)(self.tracking_region_design['YLocationMultiplier'].iat[0])==-1):
                title = title + " (Y Coordinate Fipped)"
        plt.title(title)
        
        tmp = self.get_plot_limits()
        plt.ylim(tmp[1])
        plt.grid(True)
        plt.show()
      
    def summarize(self, range_minutes=(0,0)):
        data_subset = self.get_data_subset(range_minutes)

        if(len(data_subset)==0):
            ## An empty window is reported as missing rather than crashing on .iat[0].
            obs_minutes = pd.NA
            start_minutes = pd.NA
            end_minutes = pd.NA
        else:
            obs_minutes = windowing.observed_minutes(data_subset)
            start_minutes = data_subset['Minutes'].iat[0]
            end_minutes = data_subset['Minutes'].iat[-1]

        if(self.tracking_region_design is not None):
            treatment = self.tracking_region_design['Treatment'].iat[0]
        else:
            treatment = ''

        result = pd.Series([treatment, self.name,self.tracking_region_id,obs_minutes,start_minutes,end_minutes])
        result.index = ['Treatment','Name','TrackingRegion','ObsMinutes','StartMinutes','EndMinutes']
        return result
   
    