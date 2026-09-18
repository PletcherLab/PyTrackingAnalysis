import logging

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.animation import FuncAnimation

from . import clock_repair
from . import windowing

logger = logging.getLogger(__name__)


class Tracker:
    def __init__(self, tracking_region_id, object_id, tracking_regions, counting_regions, parameters, exp_design,rawdata):
        """
        Initialize the Tracker object.
        Generally not called by the user.

        Parameters:
        tracking_region_id (str): Identifier for the tracking region.
        object_id (str): Identifier for the object being tracked.
        tracking_regions (DataFrame): DataFrame containing tracking region information.
        counting_regions (DataFrame): DataFrame containing counting region information.
        parameters (object): Parameters for the tracking analysis.
        exp_design (object): Experimental design information.
        rawdata (DataFrame): Raw data for the tracking.
        """
        ## These are the key identifiers for the tracker.  They are defined by the data file.
        self.tracking_region_id = tracking_region_id
        self.object_id = object_id
        ## Contact the two identifying features into a unique name for the tracking blob.
        self.name = f'{tracking_region_id}_{object_id}'
        self.rawdata = rawdata        
        ## We are setting the index to Frame so that operations between trackers always line up with the Frame.
        ## Them make sure we are in ascending Frame order, which should be true anyway, but just to be sure.
        ## drop=False keeps the Frame column available; calculate_minutes needs it when fps is set.
        self.rawdata = self.rawdata.set_index('Frame', drop=False).sort_index()
        self.parameters =parameters        
        
        ## Now to get the information from the experiment design file (new format).  This is used in the summary function and 
        ## will likely be used in all stats like functions that combine replicates within a treatment group.        
        if(exp_design is not None):
            self.tracking_region_design = exp_design.get_tracking_region(self.tracking_region_id)
            self.counting_regions_design = exp_design.counting_regions
        else:
            self.tracking_region_design = None
            self.counting_regions_design = None

        ## get_tracking_region returns an *empty* DataFrame — never None — for a
        ## region that appears in the data but not in tracking_config.yaml. Every
        ## caller guards with `is not None` and then indexes [0], so that empty
        ## frame used to abort the entire run with a bare IndexError naming
        ## neither the region nor the config file. Substitute an explicit
        ## unassigned row instead: the run continues, the region is visible in
        ## every summary, and the statistics layer excludes blank treatments
        ## with a warning rather than treating them as an arm.
        if self.tracking_region_design is not None and len(self.tracking_region_design) == 0:
            logger.warning(
                "Tracking region '%s' is present in the data but missing from the "
                "experimental design; treating it as unassigned.", self.tracking_region_id
            )
            self.tracking_region_design = pd.DataFrame(
                [[self.tracking_region_id, '', 1, 1]],
                columns=['RegionName', 'Treatment', 'XLocationMultiplier', 'YLocationMultiplier'],
            )
        
        ## These are the ROI from the experiment file.  They are used for plotting and presumably for centrophobism stuff.
        self.tracking_region_roi = tracking_regions[tracking_regions['Name']==tracking_region_id].reset_index(drop=True)        
        self.counting_regions_roi = counting_regions

        ## These functions should come after all the parameters are set.        
        self.calculate_minutes()
        self.calculate_speeds_and_feeds()

    def calculate_minutes(self):
        """
        Calculate the minutes based on either the actual time between frames or the frames per second (fps) parameter.
        Generally not called by the user.
        """
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
                f"Invalid fps {self.parameters.fps} for tracker {self.name}. "
                "Use -1 (wall-clock timestamps), 0 (DTrack MSec column), or a positive frame rate."
            )
        ## A recording clock set back mid-run (an hourly time sync undoing a
        ## time-zone mismatch, in NOMPc/F-max1) sends Minutes backwards while
        ## Frame carries on. Refuse it here, where the message can still name
        ## the cause and the fix, instead of letting it surface as an empty
        ## phase, a wholesale exclusion and a matplotlib grid with zero rows.
        clock_repair.raise_if_rolled_back(
            self.name, self.rawdata['Frame'], self.rawdata['Minutes'] * 60_000)
        return

    def calculate_speeds_and_feeds(self):
        """
        Calculate the speeds and feeds for the tracked object.
        Generally not called by the user.
        """
        if(len(self.rawdata)==0):
            raise ValueError(f"Tracker {self.name} has no rows of data.")
        self.rawdata['Xpos_mm'] = self.rawdata['RelX']*self.parameters.mm_per_pixel
        self.rawdata['Ypos_mm'] = self.rawdata['RelY']*self.parameters.mm_per_pixel
        deltax = self.rawdata['Xpos_mm'].diff()
        deltay = self.rawdata['Ypos_mm'].diff()
        deltax.iat[0]=0
        deltay.iat[0]=0
        self.rawdata['Dist_mm'] = (deltax**2 + deltay**2)**0.5
        ## X-only travel, kept alongside Dist_mm so windowed sums of the two use
        ## the same step-attribution rule (see pytrackinganalysis.windowing).
        self.rawdata['XDist_mm'] = deltax.abs()
        self.rawdata['DeltaSec'] = self.rawdata['Minutes'].copy().diff() * 60
        self.rawdata.loc[self.rawdata.index[0],'DeltaSec']=0
        ## A single-frame tracker (or one whose timestamps never advance) has no
        ## sampling interval to derive a rolling window from; fall back to per-row speed.
        mean_delta_sec = self.rawdata['DeltaSec'].mean()
        if(pd.isna(mean_delta_sec) or mean_delta_sec<=0):
            window_size = 1
        else:
            window_size = int(round(1/mean_delta_sec,0) * self.parameters.speed_window_seconds)
        if(window_size<=1):
            self.rawdata['Speed_mm_sec'] = self.rawdata['Dist_mm']/self.rawdata['DeltaSec']
        else:
            self.rawdata['DistWindow_mm']  = self.rawdata['Dist_mm'].rolling(window=window_size).sum()
            self.rawdata.loc[self.rawdata.index[0],'DistWindow_mm']=0
            self.rawdata['DeltaSecWindow'] = self.rawdata['DeltaSec'].rolling(window=window_size).sum()
            self.rawdata.loc[self.rawdata.index[0],'DeltaSecWindow']=0
            self.rawdata['Speed_mm_sec']  = self.rawdata['DistWindow_mm']/self.rawdata['DeltaSecWindow']
            self.rawdata.loc[self.rawdata.index[0], 'Speed_mm_sec'] = 0

        ## A frame whose speed is undefined — a duplicate or stalled timestamp
        ## giving DeltaSec == 0, or the lead-in rows of the rolling window — is
        ## not a measurement of anything. NaN comparisons are False, so such a
        ## frame used to fall through to IsResting=True and be reported as the
        ## animal resting. Track it explicitly instead so the activity fractions
        ## stay interpretable: "we could not tell" is not "it rested".
        self.rawdata['IsUnmeasurable'] = self.rawdata['Speed_mm_sec'].isna()
        measurable = ~self.rawdata['IsUnmeasurable']

        self.rawdata['IsWalking'] = self.rawdata['Speed_mm_sec'] > self.parameters.walking_speed_mm_sec
        self.rawdata['IsMicroMove'] = (self.rawdata['Speed_mm_sec'] > self.parameters.micro_move_speed_mm_sec[0]) & (self.rawdata['Speed_mm_sec'] < self.parameters.micro_move_speed_mm_sec[1])
        self.rawdata['IsResting'] = measurable
        ## Resting is not walking or micro move but can be altered by sleep.
        self.rawdata.loc[self.rawdata['IsWalking']==True,'IsResting'] = False
        self.rawdata.loc[self.rawdata['IsMicroMove']==True,'IsResting'] = False


        self.calculate_sleeping()
        
    ## This is definitely beta code at the moment. But it may be working reasonably well. It requires a good value for the lower bound of micro move speed.
    def calculate_sleeping(self):
        """
        Calculate the sleeping periods for the tracked object.
        This is a beta feature and may not work as expected.
        Generally not called by the user.
        """
        self.rawdata['IsSleeping'] = False
        runs = self.calculate_run_boundaries() 
        for i,run in runs.iterrows():
            if(run['IsSleeping']):
                self.rawdata.loc[run['start']:run['end'],'IsResting'] = False
                self.rawdata.loc[run['start']:run['end'],'IsSleeping'] = True

    def calculate_run_boundaries(self):
        """
        Calculate the run boundaries for the tracked object.
        Generally not called by the user.
        Returns:
        DataFrame: DataFrame containing the start, end, and duration of each run.
        """
        # Ensure the series is boolean
        series=self.rawdata['IsResting'].copy()
    
        # Identify where the value changes
        change_points = series.diff().fillna(0).astype(bool)
    
        # Create a group identifier for each run
        run_id = change_points.cumsum()
    
        # Group by the run identifier and calculate the start and end indices
        run_boundaries = series.groupby(run_id).apply(lambda x: (x.index[0], x.index[-1], x.iloc[0])).reset_index(drop=True)
        run_boundaries.columns = ['start', 'end', 'value']
        run_boundaries = pd.DataFrame(run_boundaries.tolist(),columns=['start','end','value'])

        run_boundaries['EndMin'] = self.rawdata.loc[run_boundaries['end'], 'Minutes'].reset_index(drop=True)
        run_boundaries['StartMin'] = self.rawdata.loc[run_boundaries['start'], 'Minutes'].reset_index(drop=True)
        run_boundaries['DurationMin'] = run_boundaries['EndMin'] - run_boundaries['StartMin']
        run_boundaries['IsSleeping'] = (run_boundaries['DurationMin']>=self.parameters.sleep_threshold_min) & run_boundaries['value']==True
        
        return run_boundaries
    
    def get_data_quality(self, range_minutes=(0,0)):
        """
        Calculate the percentages of rows with each value in the DataQuality column.

        An empty window yields a row of NA rather than a divide-by-zero, so a
        tracker with no data in the requested range stays visible in the report
        instead of aborting it.

        Returns:
        DataFrame: DataFrame containing the DataQuality values and their corresponding percentages.
        """
        data_subset = self.get_data_subset(range_minutes)
        if(len(data_subset)==0):
            return pd.DataFrame({
                'HighQuality': [pd.NA],
                'NotFound': [pd.NA],
                'Indiscernible': [pd.NA],
                'StartMinutes': [pd.NA],
                'EndMinutes': [pd.NA]
            })

        nrows = len(data_subset)
        perc_highquality = (data_subset['DataQuality']=='High').sum()/nrows
        perc_notfound = (data_subset['DataQuality']=='NotFound').sum()/nrows
        perc_indiscernable = (data_subset['DataQuality']=='Indiscernible').sum()/nrows
        start_minutes = data_subset['Minutes'].iat[0]
        end_minutes = data_subset['Minutes'].iat[-1]

        # Create a DataFrame with the results
        df_percentages = pd.DataFrame({
            'HighQuality': [perc_highquality],
            'NotFound': [perc_notfound],
            'Indiscernible': [perc_indiscernable],
            'StartMinutes': [start_minutes],
            'EndMinutes': [end_minutes]
        })

        return df_percentages

    def get_data_subset(self, range_minutes):
        """
        Get a subset of the raw data within the specified range of minutes.

        The window is half-open — ``[start, end)`` — so consecutive facet
        phases partition the recording instead of both claiming the row that
        lands on their shared boundary. ``(0, 0)`` means the whole recording.

        ``Dist_mm`` and ``DeltaSec`` are recomputed for the window. Each holds
        the step *arriving* at its row, so the first row of a window measures
        against the last row before the window. Facet phases therefore sum to
        the whole-recording total: a step spanning a cutoff is counted once, in
        the phase it arrives in.

        ``Speed_mm_sec`` is deliberately left as computed over the full
        recording — it is a rolling instantaneous measure, not an accumulation.

        Parameters:
        range_minutes (tuple): Tuple of two numbers specifying the start and end minutes.

        Returns:
        DataFrame: Copy of the raw data within the specified range.
        """
        return windowing.slice_with_window_deltas(self.rawdata, range_minutes)

    def summarize(self, range_minutes=(0,0)):
        """
        Summarize the tracking data within the specified range of minutes.

        Parameters (optional):
        range_minutes (tuple): Tuple of two integers specifying the start and end minutes.

        Returns:
        Series: Summary statistics of the tracking data.
        """
        data_subset = self.get_data_subset(range_minutes)
        if(len(data_subset)!=0):
            ## Activity fractions are denominated over *measurable* frames only.
            ## A frame with no defined speed cannot be classified, and folding it
            ## into the denominator as "resting" makes "the animal was still"
            ## indistinguishable from "we could not tell". PercUnmeasurable is
            ## reported over all frames so the two are always separable.
            unmeasurable = data_subset['IsUnmeasurable'] if 'IsUnmeasurable' in data_subset.columns \
                else pd.Series(False, index=data_subset.index)
            n_measurable = int((~unmeasurable).sum())
            perc_unmeasurable = int(unmeasurable.sum())/len(data_subset)
            if n_measurable > 0:
                measured = data_subset.loc[~unmeasurable]
                perc_sleeping = measured['IsSleeping'].sum()/n_measurable
                perc_walking = measured['IsWalking'].sum()/n_measurable
                perc_micro = measured['IsMicroMove'].sum()/n_measurable
                perc_resting = measured['IsResting'].sum()/n_measurable
            else:
                perc_sleeping = perc_walking = perc_micro = perc_resting = pd.NA

            avg_speed = data_subset['Speed_mm_sec'].mean()
            total_distance = data_subset['Dist_mm'].sum()
            obs_minutes = windowing.observed_minutes(data_subset)
            start_minutes = data_subset['Minutes'].iat[0]
            end_minutes = data_subset['Minutes'].iat[-1]
            ## A single-row or zero-duration window has no rate to report.
            total_distance_min = windowing.safe_rate(total_distance, obs_minutes)

            perc_highquality = (data_subset['DataQuality']=='High').sum()/len(data_subset)

            ## TotalDistance sums every step in the window, including steps into
            ## and out of frames where tracking was lost — those carry real
            ## coordinate jumps, not NaN, and inflate the total by up to ~19% per
            ## animal in proportion to how poor the tracking was. Report the
            ## quality-filtered total alongside it rather than silently changing
            ## the headline number, so a user can see the size of the correction
            ## on their own data. A step is discarded if either endpoint was lost.
            lost = data_subset['DataQuality'] != 'High'
            contaminated = lost | lost.shift(1, fill_value=False)
            total_distance_hq = data_subset.loc[~contaminated, 'Dist_mm'].sum()
        else:
            perc_sleeping = pd.NA
            perc_walking = pd.NA
            perc_micro = pd.NA
            perc_resting = pd.NA
            perc_unmeasurable = pd.NA
            avg_speed = pd.NA
            total_distance = pd.NA
            total_distance_hq = pd.NA
            obs_minutes = pd.NA
            start_minutes = pd.NA
            end_minutes = pd.NA
            total_distance_min = pd.NA
            perc_highquality = pd.NA

        if self.tracking_region_design is not None:
            treatment = self.tracking_region_design['Treatment'].iat[0]
        else:
            treatment = ''

        #tmp = (f"Treatment: {treatment}, Name: {self.name}, ObsMin: {obs_minutes:.2f}, Sleeping: {perc_sleeping:.2f}, Walking: {perc_walking:.2f}, Micro: {perc_micro:.2f}, Resting: {perc_resting:.2f}, AvgSpeed: {avg_speed:.2f}, TotalDist: {total_distance:.2f}, TotalDist2: {total_distance_dtrack:.2f}, StartMin: {start_minutes:.2f}, EndMin: {end_minutes:.2f}")
        result = pd.Series([treatment, self.name,self.tracking_region_id,self.object_id,obs_minutes,total_distance,total_distance_hq,total_distance_min,perc_sleeping,perc_walking,perc_micro,perc_resting,perc_unmeasurable,avg_speed,perc_highquality,start_minutes,end_minutes])
        result.index = ['Treatment','Name','TrackingRegion','ObjectID','ObsMinutes','TotalDistance','TotalDistanceHighQualityOnly','TotalDistancePerMin','PercSleeping','PercWalking','PercMicro','PercResting','PercUnmeasurable','AvgSpeed','PercHighQuality','StartMinutes','EndMinutes']
        return result

    def get_plot_limits(self):
        """
        Get the plot limits for the tracking region.
        Generally not called by user.
        Returns:
        tuple: Tuple containing the x and y limits for the plot.
        """
        xlims=(self.tracking_region_roi['Width'].values[0]*self.parameters.mm_per_pixel)/(-2.0),(self.tracking_region_roi['Width'].values[0]*self.parameters.mm_per_pixel)/(2.0)
        ylims=(self.tracking_region_roi['Height'].values[0]*self.parameters.mm_per_pixel)/(-2.0),(self.tracking_region_roi['Height'].values[0]*self.parameters.mm_per_pixel)/(2.0)
        return xlims,ylims
        
    def plot_x(self, range_minutes=(0,0), show_light=False):
        """
        Plot the X position of the tracked object over time.

        Parameters:
        range_minutes (tuple): Tuple of two integers specifying the start and end minutes.
        show_light (bool): Whether to show light indicators on the plot.
        """
        if(show_light):
            data_subset = self.get_data_subset(range_minutes)            
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(data_subset['Minutes'], data_subset['Xpos_mm']*(int)(self.tracking_region_design['XLocationMultiplier'].iloc[0]), label=self.name)
            ax.set_xlabel('Minutes')
            ax.set_ylabel('Position (mm)')
            title = f'{self.name} ({self.tracking_region_design["Treatment"].iloc[0]})'
            if((int)(self.tracking_region_design['XLocationMultiplier'].iloc[0])==-1):
                title = title + " (X Coordinate Flipped)"
            ax.set_title(title)
            ax.legend()      
            tmp = self.get_plot_limits()        
            ax.set_ylim(tmp[0])
            ax.grid(True)

            for i in range(len(data_subset) - 1):
              if data_subset['Indicator'].iloc[i]>0:
                ax.axvspan(data_subset['Minutes'].iloc[i], data_subset['Minutes'].iloc[i + 1], color='red', alpha=0.1)
            plt.show()
        else:
            data_subset = self.get_data_subset(range_minutes)
            plt.figure(figsize=(10, 6))
            plt.plot(data_subset['Minutes'], data_subset['Xpos_mm']*(int)(self.tracking_region_design['XLocationMultiplier'].iloc[0]), label=self.name)
            plt.xlabel('Minutes')
            plt.ylabel('Position (mm)')
            title = f'{self.name} ({self.tracking_region_design["Treatment"].iloc[0]})'
            if((int)(self.tracking_region_design['XLocationMultiplier'].iloc[0])==-1):
                title = title + " (X Coordinate Flipped)"
            plt.title(title)
            plt.legend()
            tmp = self.get_plot_limits()        
            plt.ylim(tmp[0])
            plt.grid(True)
            plt.show()

    def plot_x_hist(self, bins=30, range_minutes=(0, 0)):
        """
        Plot a histogram of the X positions of the tracked object.

        Parameters (opotional):
        bins (int): Number of bins for the histogram.
        range_minutes (tuple): Tuple of two integers specifying the start and end minutes.
        """
        data_subset = self.get_data_subset(range_minutes)
        
        plt.figure(figsize=(10, 6))
        plt.hist(data_subset['Xpos_mm']*(int)(self.tracking_region_design['XLocationMultiplier'].iloc[0]), bins=bins, edgecolor='black')
        plt.xlabel('X Position (mm)')
        plt.ylabel('Frequency')
        title = f'{self.name} ({self.tracking_region_design["Treatment"].iat[0]})'
        if((int)(self.tracking_region_design['XLocationMultiplier'].iat[0])==-1):
                title = title + " (X Coordinate Flipped)"
        plt.title(title)
        tmp = self.get_plot_limits()
        plt.xlim(tmp[0])
        plt.grid(True)
        plt.show()

    def plot_total_distance(self, range_minutes=(0,0), show_light=False):
        """
        Plot the total distance traveled by the tracked object over time.

        Parameters (optional):
        range_minutes (tuple): Tuple of two integers specifying the start and end minutes.
        show_light (bool): Whether to show light indicators on the plot.
        """
        if(show_light):
            data_subset = self.get_data_subset(range_minutes)
            cum_dist = data_subset['Dist_mm'].cumsum()
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(data_subset['Minutes'], cum_dist, label=self.name)
            ax.set_xlabel('Minutes')
            ax.set_ylabel('Total Distance (mm)')
            ax.set_title(f'{self.name} ({self.tracking_region_design["Treatment"].iloc[0]})')
            ax.legend()
            ax.grid(True)

            for i in range(len(data_subset) - 1):
              if data_subset['Indicator'].iloc[i]>0:
                ax.axvspan(data_subset['Minutes'].iloc[i], data_subset['Minutes'].iloc[i + 1], color='red', alpha=0.1)
            plt.show()
        else:
            data_subset = self.get_data_subset(range_minutes)
            cum_dist = data_subset['Dist_mm'].cumsum()
            plt.figure(figsize=(10, 6))
            plt.plot(data_subset['Minutes'], cum_dist, label=self.name)
            plt.xlabel('Minutes')
            plt.ylabel('Total Distance (mm)')
            plt.title(f'{self.name} ({self.tracking_region_design["Treatment"].iloc[0]})')
            plt.legend()
            plt.grid(True)
            plt.show()

    def plot_y(self, range_minutes=(0,0), show_light=False):
        """
        Plot the Y position of the tracked object over time.

        Parameters (optional):
        range_minutes (tuple): Tuple of two integers specifying the start and end minutes.
        show_light (bool): Whether to show light indicators on the plot.
        """
        if(show_light):
            data_subset = self.get_data_subset(range_minutes)            
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(data_subset['Minutes'], data_subset['Ypos_mm']*(int)(self.tracking_region_design['YLocationMultiplier'].iloc[0]), label=self.name)
            ax.set_xlabel('Minutes')
            ax.set_ylabel('Position (mm)')
            title = f'{self.name} ({self.tracking_region_design["Treatment"].iloc[0]})'
            if((int)(self.tracking_region_design['YLocationMultiplier'].iloc[0])==-1):
                title = title + " (Y Coordinate Flipped)"
            ax.set_title(title)
            ax.legend()      
            tmp = self.get_plot_limits()        
            ax.set_ylim(tmp[1])
            ax.grid(True)

            for i in range(len(data_subset) - 1):
              if data_subset['Indicator'].iloc[i]>0:
                ax.axvspan(data_subset['Minutes'].iloc[i], data_subset['Minutes'].iloc[i + 1], color='red', alpha=0.1)
            plt.show()
        else:
            data_subset = self.get_data_subset(range_minutes)
            plt.figure(figsize=(10, 6))        
            plt.plot(data_subset['Minutes'], data_subset['Ypos_mm']*(int)(self.tracking_region_design['YLocationMultiplier'].iloc[0]), label='Y Position (mm)')
            plt.xlabel('Minutes')
            plt.ylabel('Position (mm)')
            title = f'{self.name} ({self.tracking_region_design["Treatment"].iloc[0]})'            
            if((int)(self.tracking_region_design['YLocationMultiplier'].iloc[0])==-1):
                title = title + " (Y Coordinate Flipped)"
            plt.title(title)
            plt.legend()
            tmp = self.get_plot_limits()
            plt.ylim(tmp[1])
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
        scatter = plt.scatter(data_subset['Xpos_mm']*(int)(self.tracking_region_design['XLocationMultiplier'].iloc[0]), data_subset['Ypos_mm']*(int)(self.tracking_region_design['YLocationMultiplier'].iloc[0]), c=data_subset['Minutes'], cmap='viridis', vmin=data_subset['Minutes'].min(), vmax=data_subset['Minutes'].max())
        plt.colorbar(scatter, label='Minutes')
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

    def plot_xy_animated(self, range_minutes=(0, 0), interval = .1, tail_size = 100000000):
        """
        Plot an animated scatter plot of the XY positions of the tracked object.

        Parameters:
        range_minutes (tuple): Tuple of two integers specifying the start and end minutes.
        interval (float): Interval between frames in the animation.
        tail_size (int): Number of points to show in the tail of the animation.
        """
        x_locations = self.get_x_positions(range_minutes)
        y_locations = self.get_y_positions(range_minutes)        
        minutes = self.get_minutes(range_minutes)

        fig, ax = plt.subplots(figsize=(10, 6))
        scatter = ax.scatter([], [], c=[], cmap='viridis', vmin=minutes.min(), vmax=minutes.max())
        colorbar = plt.colorbar(scatter, ax=ax, label='Minutes')
        ax.set_xlabel('X Position (mm)')
        ax.set_ylabel('Y Position (mm)')
        title = f'{self.name} ({self.tracking_region_design["Treatment"].iloc[0]})'
        if((int)(self.tracking_region_design['YLocationMultiplier'].iloc[0])==-1 and (int)(self.tracking_region_design['XLocationMultiplier'].iloc[0])==-1):
                title = title + " (X and Y Coordinates Flipped)"
        elif((int)(self.tracking_region_design['YLocationMultiplier'].iloc[0])==-1):
                title = title + " (Y Coordinate Flipped)"
        elif((int)(self.tracking_region_design['XLocationMultiplier'].iloc[0])==-1):
                title = title + " (X Coordinate Flipped)"
        ax.set_title(title)
        xlims, ylims = self.get_plot_limits()
        ax.set_xlim(xlims)
        ax.set_ylim(ylims)
        ax.grid(True)

        if self.tracking_region_roi['Shape'].values[0] == 'Ellipse':
            ellipse = patches.Ellipse((0, 0), width=self.tracking_region_roi['Width'].values[0] * self.parameters.mm_per_pixel, height=self.tracking_region_roi['Height'].values[0] * self.parameters.mm_per_pixel, edgecolor='gray', facecolor='none', linewidth=1)
            ax.add_patch(ellipse)

        time_text = ax.text(0.02, 0.95, '', transform=ax.transAxes)

        def init():
            scatter.set_offsets([np.nan, np.nan])
            scatter.set_array([])
            time_text.set_text('')
            return scatter,time_text

        def update(frame):
            if(frame < (tail_size+1)):                
                current_x_locations = x_locations[:frame + 1]
                current_y_locations = y_locations[:frame + 1]
                current_minutes = minutes[:frame + 1]            
            else:            
                current_x_locations = x_locations[(frame-tail_size):(frame + 1)]
                current_y_locations = y_locations[(frame-tail_size):(frame + 1)]
                current_minutes = minutes[(frame-tail_size):(frame + 1)].reset_index(drop=True)            
            scatter.set_offsets(np.c_[current_x_locations, current_y_locations])
            scatter.set_array(current_minutes)
          
            time_text.set_text(f"Minutes: {current_minutes[len(current_minutes)-1]:.2f}")
            return scatter, time_text

        ani = FuncAnimation(fig, update, frames=len(x_locations), init_func=init, blit=True, repeat=False, interval=interval)
        plt.show()

    def get_x_positions(self, range_minutes=(0,0)):
        """
        Get the X positions of the tracked object within the specified range of minutes.

        Parameters (optional):
        range_minutes (tuple): Tuple of two integers specifying the start and end minutes.

        Returns:
        Series: X positions of the tracked object.
        """
        data_subset = self.get_data_subset(range_minutes)        
        return data_subset['Xpos_mm']*(int)(self.tracking_region_design['XLocationMultiplier'].iloc[0])

    def get_minutes(self, range_minutes=(0,0)):
        """
        Get the minutes within the specified range of minutes.

        Parameters (optional):
        range_minutes (tuple): Tuple of two integers specifying the start and end minutes.

        Returns:
        Series: Minutes within the specified range.
        """
        data_subset = self.get_data_subset(range_minutes)        
        return data_subset['Minutes']

    def get_y_positions(self, range_minutes=(0,0)):
        """
        Get the Y positions of the tracked object within the specified range of minutes.

        Parameters (optional):
        range_minutes (tuple): Tuple of two integers specifying the start and end minutes.

        Returns:
        Series: Y positions of the tracked object.
        """
        data_subset = self.get_data_subset(range_minutes)        
        return data_subset['Ypos_mm']*(int)(self.tracking_region_design['YLocationMultiplier'].iloc[0])

    def get_treatment(self):
        """
        Get the treatment information for the tracking region.

        Returns:
        str: Treatment information.
        """
        return self.tracking_region_design['Treatment'].iloc[0]

    def get_tracking_region_id(self):
        """
        Get the tracking region ID.

        Returns:
        str: Tracking region ID.
        """
        return self.tracking_region_id

    def __str__(self):
        """
        Return a string representation of key Tracker object information.

        Returns:
        str: String representation of the Tracker object.
        """
        #return f"a={self.rawdata['CountingRegion']}"
        #return f"Tracker(name={self.name}, fps={self.parameters.fps}, head=\n{self.rawdata.head()},tail=\n{self.rawdata.tail()})"
        tmp = self.summarize()
        tmp_str = (f"Name: {tmp['Name']}, ObsMin: {tmp['ObsMinutes']:.2f}, TotalDist: {tmp['TotalDistance']:.2f}, "
                   f"Sleeping: {tmp['PercSleeping']:.2f}, Walking: {tmp['PercWalking']:.2f}, "
                   f"Micro: {tmp['PercMicro']:.2f}, Resting: {tmp['PercResting']:.2f}, "
                   f"AvgSpeed: {tmp['AvgSpeed']:.2f}, StartMin: {tmp['StartMinutes']:.2f}, EndMin: {tmp['EndMinutes']:.2f}")
        return tmp_str