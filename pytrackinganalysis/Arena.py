import logging
import numpy as np
import pandas as pd
from . import Tracker
from . import Counter
from . import TwoChoiceTracker
from . import XChoiceTracker
from . import TwoChoiceCounter
from . import PairwiseInteractionTracker
from . import PairwiseInteractionCounter
from . import Parameters
from . import ExperimentalDesign
from . import windowing
import glob
from natsort import natsorted
import seaborn as sns
import matplotlib.pyplot as plt
from statsmodels.stats.multicomp import pairwise_tukeyhsd
from collections import OrderedDict
from scipy.stats import ttest_ind
import time
import os
import random
import yaml

logger = logging.getLogger(__name__)

def _abbrev_treatment(t):
    """Abbreviate each comma-separated factor in a treatment string to 3 characters."""
    return ", ".join(part.strip()[:3] for part in str(t).split(","))

## Sentinel standing in for NaN when comparing run labels: NaN != NaN, so a
## plain .shift() comparison would split a run of out-of-region frames into one
## run per frame.
_NO_GROUP = "\x00<none>"


def _merge_adjacent_runs(rle_df, column='group'):
    """Re-run the RLE over ``column`` so runs that became equal after a relabel fuse."""
    keys = rle_df[column].fillna(_NO_GROUP)
    changes = keys.ne(keys.shift()).cumsum()
    return rle_df.groupby(changes, as_index=False).agg({'lengths': 'sum', column: 'first'})


## Config parsing lives in Experiment, which is the object that owns the config.
## Arena used to carry a near-verbatim copy of _load_config/_build_parameters
## plus these two tables, reachable only through a `self.config` attribute that
## nothing ever set. Two copies of the rig table would have drifted; see
## Experiment._build_parameters and config_validation.RIG_ALIASES.

class NoFacetData(ValueError):
    """A faceted plot was asked for but the faceted summary has no rows."""


class Arena:
    """
    Class representing an experimental arena for tracking and analyzing data.
    """

#region ########### Initialization Functions ############
    def __init__(self, exp_name, data_path, parameters, config_path=None, force_preprocessing=False):
        """
        Initialize the Arena object.

        Parameters:
        exp_name (str): Experiment name (basename of the xlsx file without extension).
        data_path (str): Path to the directory containing the xlsx and csv files.
        parameters (Parameters): Pre-built Parameters object.
        config_path (str): Optional explicit path to tracking_config.yaml. If None, looks in the
            parent directory of data_path (i.e. the project root, one level above the data folder).
        force_preprocessing (bool): Flag to force preprocessing of data.
        """
        self.parameters = parameters
        self.experiment_name = exp_name
        self.data_path = data_path

        if config_path is None:
            parent = os.path.dirname(os.path.normpath(data_path))
            self.config_path = os.path.join(parent if parent else '.', 'tracking_config.yaml')
        else:
            self.config_path = config_path
        self.get_experiment_file_info()
        self.get_experimental_design()
        self.create_trackers(force_preprocessing)
        self.computed_summaries={}
        ## Names excluded from every summary-derived result (ADR-0003). The
        ## cache stays unfiltered; the rows are dropped on the way out of
        ## summarize(), so plots, stats and CSVs cannot disagree about who's in.
        self.excluded_names: set = set()

    def set_excluded_trackers(self, names):
        """Exclude these tracker names (the summary's ``Name`` column) from all
        summaries — and therefore from figures, statistics, and the summary CSV
        outputs. Data-quality reporting deliberately still sees every tracker
        (QC describes the recording, not the analysis population)."""
        ## `names or []` is ambiguous for a pandas Series, so spell it out.
        self.excluded_names = set() if names is None else {str(n) for n in names}

    def _drop_excluded(self, summary):
        """Filter excluded rows out of a summary on its way to a consumer."""
        if not self.excluded_names or 'Name' not in summary.columns:
            return summary
        keep = ~summary['Name'].astype(str).isin(self.excluded_names)
        return summary[keep].reset_index(drop=True)

    def _abbrev_df(self, df):
        """Return a copy of df with the Treatment column abbreviated for plot labels."""
        out = df.copy()
        out['Treatment'] = out['Treatment'].map(_abbrev_treatment)
        return out

    def _facet_grid(self, the_data, **kwargs):
        """``sns.FacetGrid`` over ``FacetRange`` that fails legibly on an empty summary.

        Zero facet columns reach matplotlib as ``GridSpec(0, ...)`` and surface
        as "Number of rows must be a positive integer, not 0" — a message about
        matplotlib internals when the real story is that nothing survived
        ``summarize_facet``: every tracker excluded, or no rows inside any window.
        """
        if len(the_data) == 0:
            excluded = getattr(self, 'excluded_names', None) or ()
            raise NoFacetData(
                "Nothing to plot: the faceted summary is empty. "
                f"{len(excluded)} of {len(self.trackers)} tracker(s) are excluded and "
                "the rest have no rows inside the facet windows. Check the exclusion "
                "notes printed at load and the facet cutoffs.")
        return sns.FacetGrid(the_data, col='FacetRange', col_wrap=3, height=4, **kwargs)

    def get_experiment_file_info(self):
        """
        Retrieve experiment file information from an Excel file.

        Generally not called by the user.

        :meta private:
        """
        file_name = self.data_path + self.experiment_name + '.xlsx'
        sheet_name = "ROI"
        
        roi = pd.read_excel(file_name, sheet_name=sheet_name)
        self.tracking_regions =  roi[(roi['Type']=='Tracking')].reset_index(drop=True)
        self.counting_regions =  roi[(roi['Type']=='Counting')].reset_index(drop=True)
     
    def get_experimental_design(self):        
        """
        Retrieve the experimental design.
        Generally not called by the user.
        
        :meta private:
        """
        self.experimental_design = ExperimentalDesign.ExperimentalDesign(
            self.data_path + self.experiment_name, self.parameters,
            config_path=self.config_path
        )

    def read_all_data(self):
        """
        Read all data from CSV files.
        Generally not called by the user.
        
        Returns:
        DataFrame: Concatenated DataFrame containing all data.

        :meta private:
        """
        ## glob.escape the directory (a bracketed folder name matched nothing
        ## and surfaced as "No objects to concatenate" deep in the load); the
        ## experiment name is escaped too, since it comes from a filename.
        csv_files = natsorted(glob.glob(
            glob.escape(self.data_path + self.experiment_name) + "_Data_*.csv"))
        #Read each CSV file into a DataFrame and store them in a list
        dataframes = [pd.read_csv(file,keep_default_na=False,na_values=['NaN']) for file in csv_files]

        # Concatenate all DataFrames into a single DataFrame
        rd = pd.concat(dataframes, ignore_index=True)
        return rd
    
    def delete_tracker(self,region, object_id):
        """
        Delete a tracker.

        Parameters:
        tracker_id (str): ID of the tracker to delete.

        :meta private:
        """
        self.trackers.pop(f'{region}_{object_id}')
        self.invalidate_summaries()

    def set_trackers(self, trackers):
        """Replace the tracker collection and drop any cached summaries.

        Cached summaries are keyed only by time range, so they would otherwise
        keep returning rows for trackers that have since been filtered out.
        Route every mutation of ``self.trackers`` through here.
        """
        self.trackers = OrderedDict(trackers)
        self.invalidate_summaries()

    def invalidate_summaries(self):
        """Forget cached summaries — call after anything that changes the tracker set."""
        ## create_trackers runs before __init__ sets up the cache.
        if hasattr(self, 'computed_summaries'):
            self.computed_summaries.clear()


    def create_trackers(self, force_preprocessing):
        """
        Create trackers based on the experimental parameters.

        Parameters:
        force_preprocessing (bool): Flag to force preprocessing of data.

        :meta private:
        """
        rawdata = self.check_for_preprocessing(self.read_all_data(),force_preprocessing)
        tmp_trackers = {}
        if(self.parameters.get_tracking_class() == Parameters.TrackingClass.TRACKING):
            design_path = os.path.join(self.data_path, self.experiment_name + '_Design.txt')
            if self.parameters.get_tracking_type() == Parameters.TrackingType.TWOCHOICETRACKER:
                if self.experimental_design is None:
                    raise ValueError(
                        "TwoChoiceTracker requires a design file with [Counting Regions]. "
                        f"Expected file not found or failed to load: {os.path.abspath(design_path)}"
                    )
                if getattr(self.experimental_design, 'counting_regions', None) is None:
                    raise ValueError(
                        "Design file has no [Counting Regions] section. "
                        f"Edit the file to add one: {os.path.abspath(design_path)}"
                    )
            grouped_data = rawdata.groupby(['TrackingRegion','ObjectID'] )
            for (region,object_id), group in grouped_data:
                if(self.parameters.get_tracking_type()==Parameters.TrackingType.TRACKER):
                    tracker = Tracker.Tracker(region,object_id,self.tracking_regions,self.counting_regions,self.parameters,self.experimental_design,group)
                elif(self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICETRACKER):
                    tracker = TwoChoiceTracker.TwoChoiceTracker(region,object_id,self.tracking_regions,self.counting_regions,self.parameters,self.experimental_design,group)
                elif(self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER):                    
                    tracker = PairwiseInteractionTracker.PairwiseInteractionTracker(region,object_id,self.tracking_regions,self.counting_regions,self.parameters,self.experimental_design,group)
                elif(self.parameters.get_tracking_type()==Parameters.TrackingType.XCHOICETRACKER):
                    tracker = XChoiceTracker.XChoiceTracker(region,object_id,self.tracking_regions,self.counting_regions,self.parameters,self.experimental_design,group)
                else:
                    raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be an instance of TrackingType enum.")
                tmp_trackers[f'{region}_{object_id}'] = tracker 
            self.trackers = OrderedDict((key, tmp_trackers[key]) for key in natsorted(tmp_trackers))
        elif(self.parameters.get_tracking_class() == Parameters.TrackingClass.COUNTING):
            design_path = os.path.join(self.data_path, self.experiment_name + '_Design.txt')
            if self.parameters.get_tracking_type() == Parameters.TrackingType.TWOCHOICECOUNTER:
                if self.experimental_design is None:
                    raise ValueError(
                        "TwoChoiceCounter requires a design file with [Counting Regions]. "
                        f"Expected file not found or failed to load: {os.path.abspath(design_path)}"
                    )
                if getattr(self.experimental_design, 'counting_regions', None) is None:
                    raise ValueError(
                        "Design file has no [Counting Regions] section. "
                        f"Edit the file to add one: {os.path.abspath(design_path)}"
                    )
            grouped_data = rawdata.groupby('TrackingRegion')
            for region, group in grouped_data:
                if(self.parameters.get_tracking_type()==Parameters.TrackingType.COUNTER):
                    counter = Counter.Counter(region,self.tracking_regions,self.counting_regions,self.parameters,self.experimental_design,group)
                elif(self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICECOUNTER):
                    counter = TwoChoiceCounter.TwoChoiceCounter(region,self.tracking_regions,self.counting_regions,self.parameters,self.experimental_design,group)
                elif(self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONCOUNTER):
                    counter = PairwiseInteractionCounter.PairwiseInteractionCounter(region,self.tracking_regions,self.counting_regions,self.parameters,self.experimental_design,group)
                else:
                    raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be an instance of TrackingType enum.")
                tmp_trackers[f'{region}'] = counter 
            self.trackers = OrderedDict((key, tmp_trackers[key]) for key in natsorted(tmp_trackers))
        else:
            raise ValueError(f"Invalid tracking class: {self.parameters.get_tracking_class()}. Must be an instance of TrackingClass enum.")
        self.check_for_postprocessing(rawdata) 

    def check_for_postprocessing(self,rawdata):
        """
        Check for postprocessing requirements.

        Parameters:
        rawdata (DataFrame): Raw data to be processed.

        :meta private:
        """
        if(self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER):
            ## Pair each tracker with the other occupant of its region. With three or
            ## more objects in a region the old loop silently kept whichever partner
            ## came last in dictionary order, so the reported "neighbour" depended on
            ## key sort order rather than on a declared pair.
            by_region = {}
            for key, tracker in self.trackers.items():
                by_region.setdefault(tracker.get_tracking_region_id(), []).append((key, tracker))
            for region, members in by_region.items():
                if len(members) != 2:
                    raise ValueError(
                        f"Tracking region '{region}' has {len(members)} object(s): "
                        f"{[k for k, _ in members]}. PAIRWISEINTERACTIONTRACKER requires "
                        "exactly two objects per tracking region."
                    )
                (_, first), (_, second) = members
                first.set_neighbor(second)
                second.set_neighbor(first)
        
    def check_for_preprocessing(self,rawdata, force_preprocessing=False):
        """
        Check for preprocessing requirements.

        Parameters:
        rawdata (DataFrame): Raw data to be processed.
        force_preprocessing (bool): Flag to force preprocessing of data.

        Returns:
        DataFrame: Processed raw data.

        :meta private: 
        """
        ## For now we will disable this.  It should be only used if the post-processing fails
        ## because the data for partner trackers does not line up.
        if ((self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER) and force_preprocessing):             
            if 'ClosestNeighbor' not in rawdata.columns:
                rawdata=self.calculate_distances_for_pairwise_tracker(rawdata)
        if ((self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONCOUNTER)):
            if 'ClosestNeighbor' not in rawdata.columns:
                rawdata=self.calculate_distances_for_pairwise_tracker(rawdata)
        return rawdata
    
    def calculate_distances_for_pairwise_tracker(self,rawdata):
        """
        Calculate distances for pairwise interaction tracker.
        This is a somewhat slow function that churns through de novo calculates of neighbor distances.
        It can be forced by force_preprocessing=True in the constructor.
        Generally not called by the user.
        Parameters:
        rawdata (DataFrame): Raw data to be processed.

        Returns:
        DataFrame: Processed raw data with distances.

        :meta private: 
        """
        ## This works but it's pretty slow.
        # Group the data by 'TrackingRegion', 'ObjectID', and 'Frame'
        print("Calculating pairwise distances will take awhile...")
        grouped_data = rawdata.groupby(['Frame', 'TrackingRegion'], sort=False)

        ## Write each group's distance back through its own row labels. Collecting
        ## the values into a flat list and assigning positionally assumed the raw
        ## rows were already in groupby order, and appended a fixed two values for
        ## the collapsed-blob case regardless of how many rows the group held.
        rawdata['ClosestNeighbor'] = np.nan

        counter = 1
        n_groups = len(grouped_data)
        print(f'Analyzing group {counter} of {n_groups}')
        for (frame, region), group in grouped_data:
            if(counter % 1000 == 0):
                print(f'Analyzing group {counter} of {n_groups}')
            counter += 1
            if len(group) == 2:
                if((group.iloc[0]['DataQuality']=="High") and (group.iloc[1]['DataQuality']=="High")):
                    x1, y1 = group.iloc[0][['X', 'Y']]
                    x2, y2 = group.iloc[1][['X', 'Y']]
                    distance = np.sqrt((x2 - x1)**2 + (y2 - y1)**2)
                    rawdata.loc[group.index, 'ClosestNeighbor'] = distance
            elif group['NObjects'].sum() == 2:
                ## The two animals were resolved as a single blob: zero separation.
                rawdata.loc[group.index, 'ClosestNeighbor'] = 0
            # Anything else stays NaN — not a usable pair observation.

        return rawdata
        
#endregion ########### Initialization Functions ############
    
    
#region ########### Access Functions ############
    
    def first_tracker(self):
        """
        Get the first tracker in the list in the arena.

        Returns:
        Tracker: The first tracker object.
        """
        tmp = list(self.trackers.keys())
        return self.trackers[tmp[0]]

    def get_tracker(self, key):
        """
        Get a tracker by key.

        Parameters:
        key (str): Key of the tracker.

        Returns:
        Tracker: The tracker object.
        """
        return self.trackers.get(key,None)

    def supports_data_quality(self):
        """True when this experiment's trackers can report per-frame data quality.

        Counter-class experiments count blobs per region and have no per-frame
        DataQuality to summarize, so QC callers should branch on this rather
        than discovering the missing method through an AttributeError.
        """
        return self.parameters.get_tracking_class() == Parameters.TrackingClass.TRACKING

    def get_random_tracker(arena):
        """
        Returns a random tracker from the arena's trackers.
        Assumes arena.trackers is a dict or list of tracker objects.
        """
        # If trackers is a dict, get the values as a list
        if isinstance(arena.trackers, dict):
            trackers = list(arena.trackers.values())
        else:
            trackers = list(arena.trackers)
        if not trackers:
            return None  # or raise an Exception if preferred
        return random.choice(trackers)
#endregion ########### Access Functions ############

#region ########### Basic Computation Functions ############
    def _output_path(self, suffix):
        """Where Arena writes ``<experiment><suffix>``.

        One place decides this. Every call site used to build the path with
        ``self.data_path + self.experiment_name``, which assumes a trailing
        separator that neighbouring lines using ``os.path.join`` do not.
        """
        return os.path.join(self.data_path, f"{self.experiment_name}{suffix}")

    def _over_facets(self, fn, cutoffs, **kwargs):
        """Call *fn* once per facet window and stack the results.

        The ``.copy()`` is load-bearing: ``summarize`` returns its cache entry,
        and writing ``FacetRange`` into that object leaked the column into every
        later flat read of the same window.
        """
        frames = []
        for window in windowing.facet_windows(cutoffs):
            tmp = fn(range_minutes=window, **kwargs).copy()
            tmp['FacetRange'] = [window] * len(tmp)
            frames.append(tmp)
        return pd.concat(frames, ignore_index=True)

    def summarize_facet(self,cutoffs=None,copy_to_clipboard=False, write_to_csvfile=False, remove_partners=False):
        """
        Summarize data in facets.

        Parameters:
        cutoffs (number|sequence): Facet boundary minutes. Windows are half-open,
            so every row of the recording belongs to exactly one phase.
        copy_to_clipboard (bool): Flag to copy results to clipboard.
        write_to_csvfile (bool): Flag to write results to CSV file.
        remove_partners (bool): Flag to remove partners from the summary. This is relevant for pairwise data where pairs may have identical values (e.g., interaction percentage).

        Returns:
        DataFrame: Summarized data.
        """
        all_summaries = self._over_facets(self.summarize, cutoffs,
                                          remove_partners=remove_partners)
        if(copy_to_clipboard):
            all_summaries.to_clipboard(index=False, na_rep='NA')
        if(write_to_csvfile==True):
            all_summaries.to_csv(self._output_path("_Summary_Facet_NA.csv"),
                                 index=False, na_rep='NA')
        return all_summaries
    
    
    def print_short_data_quality_report(self,cutoff=0.9,range_minutes=(0,0)):
        """
        Print a short data quality report.

        Parameters:
        range_minutes (tuple): Range of minutes to summarize.

        Returns:
        DataFrame: Short data quality report.
        """
        data_quality = self.get_data_quality(range_minutes)
        print(data_quality)

        ## get_data_quality returns pd.NA for a tracker with no frames in the
        ## window. `NA < cutoff` is NA, which boolean indexing reads as False,
        ## so a tracker that contributed *zero* data used to pass QC silently —
        ## a false negative in exactly the case QC exists to catch (a dead
        ## animal, a truncated recording, a mis-set facet cutoff).
        high_quality = pd.to_numeric(data_quality['HighQuality'], errors='coerce')
        warning_data = data_quality[high_quality.isna() | (high_quality < cutoff)]
        if(len(warning_data)>0):
            print("****************************************************")
            print(f"Warning: Some data quality falls below {cutoff}, or no data in range!")
            print(warning_data)
            print("****************************************************")
        else:
            print("****************************************************")
            print(f"All data quality is above {cutoff}.")            
            print("****************************************************")            
            
    
    def get_data_quality(self,range_minutes=(0,0)):
        """
        Print data quality.

        Generally not called by the user.
        
        :meta private:
        """
        
        if not self.supports_data_quality():
            raise ValueError(
                f"Data-quality reporting is not available for {self.parameters.get_tracking_type()}. "
                "Counter-class experiments record region occupancy, not per-frame tracking quality."
            )

        combined_results = []

        for key, tracker in self.trackers.items():
            data_quality = tracker.get_data_quality(range_minutes)
            data_quality['Tracker'] = key  # Add a column to identify the tracker
            combined_results.append(data_quality)

        # Concatenate all results into a single DataFrame
        combined_df = pd.concat(combined_results, ignore_index=True)

        # Move the last column to the first position
        last_column = combined_df.pop(combined_df.columns[-1])
        combined_df.insert(0, last_column.name, last_column)

        return combined_df        

    def summarize(self,range_minutes=(0,0),copy_to_clipboard=False, write_to_csvfile=False, remove_partners=False, include_excluded=False):
        """
        Summarize data.

        Parameters:
        range_minutes (tuple): Range of minutes to summarize.
        copy_to_clipboard (bool): Flag to copy results to clipboard.
        write_to_csvfile (bool): Flag to write results to CSV file.
        remove_partners (bool): Flag to remove partners from the summary.This is relevant for pairwise data where pairs may have identical values (e.g., interaction percentage).
        include_excluded (bool): Keep rows for trackers named in
            ``set_excluded_trackers`` (ADR-0003). Only the exclusion audit
            itself should pass True; every analysis consumer gets the filtered
            population by default.

        Returns:
        DataFrame: Summarized data.
        """
        ## Remember when returning partners we don't take shortcuts to avoid confustion.
        cache_key = tuple(range_minutes)
        if(remove_partners==False):
            if(cache_key in self.computed_summaries):
                ## Hand out a copy: summarize_facet writes a FacetRange column
                ## into whatever it gets back, which used to leak straight into
                ## the cached flat summary and out again through the next
                ## summarize(write_to_csvfile=True).
                cached = self.computed_summaries[cache_key].copy()
                return cached if include_excluded else self._drop_excluded(cached)


        summaries = []
        for key, tracker in self.trackers.items():
            summary = tracker.summarize(range_minutes)
            summaries.append(summary)
    
        # Concatenate all summaries into a single DataFrame
        all_summaries = pd.DataFrame(summaries)
        
        if(remove_partners):
            all_summaries = all_summaries.drop_duplicates(subset="TrackingRegion",keep="first")
            all_summaries.reset_index(drop=True, inplace=True)
        else:
            ## To avoid confusion, if we remove partners, we won't save a copy to speed things up.
            self.computed_summaries[cache_key] = all_summaries.copy()

        ## The cache above stays unfiltered; excluded rows are dropped on the
        ## way out so the clipboard/CSV outputs match what the plots and stats
        ## see (ADR-0003).
        if not include_excluded:
            all_summaries = self._drop_excluded(all_summaries)

        ## Note that for the this function to work in linux, you need to install xclip or xsel (verified with xclip)
        if(copy_to_clipboard):
            all_summaries.to_clipboard(index=False, na_rep='NA')
        if(write_to_csvfile==True):
            all_summaries.to_csv(self._output_path("_Summary.csv"), index=False, na_rep='NA')
        return all_summaries
    
#endregion ########### Basic Computation Functions ############

#region ########### User Plotting Functions ############
    def plot_pi(self, range_minutes=(0,0)):      
        """
        Plot preference index (PI).

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        if(self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICETRACKER):
            self.plot_pi_twochoicetracker(range_minutes)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be a TwoChoiceTracker.")
    def plot_pi_facet(self,cutoffs=None):
        """
        Plot PI in facets.

        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        if(self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICETRACKER):
            self.plot_pi_facet_twochoicetracker(cutoffs)
        elif(self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICECOUNTER):
            self.plot_pi_facet_twochoicecounter(cutoffs)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be a TwoChoiceTracker.")
    def plot_percentage(self, range_minutes=(0,0)):      
        """
        Plot choice percentage.

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        if(self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICETRACKER):
            self.plot_percentage_twochoicetracker(range_minutes)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be a TwoChoiceTracker.")  
    def plot_percentage_facet(self,cutoffs=None):
        """
        Plot choice percentage in facets.

        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        if(self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICETRACKER):
            self.plot_percentage_facet_twochoicetracker(cutoffs)
        elif(self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICECOUNTER):
            self.plot_percentage_facet_twochoicecounter(cutoffs)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be a TwoChoiceTracker.")
    def plot_adjusted_x_position(self,range_minutes=(0,0)):
        """
        Plot adjusted X position (adjusted for polarity as defined in the experimental design).

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        if(self.parameters.get_tracking_type()==Parameters.TrackingType.XCHOICETRACKER):
            self.plot_adj_x_pos_xchoicetracker(range_minutes)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be an XChoiceTracker.")
    def plot_adjusted_x_position_facet(self,cutoffs=None):
        """
        Plot adjusted X position (adjusted for polarity in the experimental design file) in facets.

        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        if(self.parameters.get_tracking_type()==Parameters.TrackingType.XCHOICETRACKER):
            self.plot_adj_x_pos_facet_xchoicetracker(cutoffs)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be an XChoiceTracker.")
    ## Every tracking type whose summary carries TotalDistancePerMin. The Hub,
    ## the Script Editor and Experiment.save_plots all advertise a total-distance
    ## plot for these; leaving one out here made the button silently do nothing.
    _DISTANCE_TRACKING_TYPES = (
        Parameters.TrackingType.TRACKER,
        Parameters.TrackingType.TWOCHOICETRACKER,
        Parameters.TrackingType.XCHOICETRACKER,
        Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER,
    )

    def plot_totaldistance(self, range_minutes=(0,0)):
        """
        Plot total distance moved (mm).

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        if(self.parameters.get_tracking_type() in self._DISTANCE_TRACKING_TYPES):
            self.plot_totaldistance_generaltracker(range_minutes)
        else:
            raise ValueError(
                f"Invalid tracking type: {self.parameters.get_tracking_type()}. "
                "Total distance requires a tracker-class experiment."
            )
    def plot_totaldistance_facet(self,cutoffs=None):
        """
        Plot total distance moved (mm) in facets.

        Parameters:
        cutoffs (number|sequence): Facet boundary minutes.
        """
        if(self.parameters.get_tracking_type() in self._DISTANCE_TRACKING_TYPES):
            self.plot_totaldistance_facet_generaltracker(cutoffs)
        else:
            raise ValueError(
                f"Invalid tracking type: {self.parameters.get_tracking_type()}. "
                "Total distance requires a tracker-class experiment."
            )

    def plot_trackers_percentages(self,window_size_min=10,step_size_min=5,range_minutes=(0,0), show_light=False):
        """
        Plot cumulative and time-dependent choice percentages on the same plot and for each tracker individually.

        Parameters:
        window_size_min (int): Window size in minutes for time dependent analysis.
        step_size_min (int): Step size in minutes for time dependent analysis.
        range_minutes (tuple): Range of minutes to plot.
        show_light (bool): Flag to show light.
        """
        if((self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICETRACKER) or \
            (self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICECOUNTER)):
            for key, tracker in self.trackers.items():
                tracker.plot_percentages(window_size_min,step_size_min,range_minutes,show_light)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be a TwoChoiceTracker.")
    
    def plot_trackers_pis(self,window_size_min=10,step_size_min=5,range_minutes=(0,0), show_light=False):
        """
        Plot trackers' cumulative and time-dependent preference indices (PIs) on the same plot and for each tracker individually.

        Parameters:
        window_size_min (int): Window size in minutes for time dependent analysis.
        step_size_min (int): Step size in minutes for time dependent analysis.
        range_minutes (tuple): Range of minutes to plot.
        show_light (bool): Flag to show light.
        """
        if((self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICETRACKER) or \
            (self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICECOUNTER)):
            for key, tracker in self.trackers.items():
                tracker.plot_pis(window_size_min,step_size_min,range_minutes,show_light)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be a TwoChoiceTracker.")

    def plot_trackers_x(self,range_minutes=(0,0),one_plot=False):
        """
        Plot each tracker's X positions. This function automatically adjusts the X positions based on the experimental design file.

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        one_plot (bool): Flag to plot all trackers in one plot.
        """
        if(self.parameters.get_tracking_class() == Parameters.TrackingClass.COUNTING): 
            raise ValueError(f"Invalid tracking class: {self.parameters.get_tracking_class()}. Must be a Tracker.")
        if(one_plot):
            for treatment in self.experimental_design.tracking_regions['Treatment'].unique():
                fig, ax = plt.subplots(figsize=(10, 6))

                ## Only the matching-treatment trackers are drawn, so the colour
                ## cycle must be sized and indexed over those — not over every
                ## tracker in the arena, which spread one treatment's traces
                ## across an arbitrary slice of the palette.
                matching = [(key, t) for key, t in self.trackers.items()
                            if t.get_treatment() == treatment]
                ## plt.cm.get_cmap was removed in matplotlib 3.9.
                colormap = plt.get_cmap('tab10').resampled(max(len(matching), 1))

                for idx, (key, tracker) in enumerate(matching):
                    x_positions = tracker.get_x_positions(range_minutes)
                    minutes = tracker.get_minutes(range_minutes)
                    ax.plot(minutes,x_positions, label=key, color=colormap(idx),alpha=0.7)

                ax.set_xlabel('Minutes')
                ax.set_ylabel('X Position')
                title = treatment + " (Axis flips applied if specified)"
                ax.set_title(title)            
                plt.show()            
        else:                     
            for key, tracker in self.trackers.items():
                tracker.plot_x(range_minutes)            

    def plot_trackers_y(self,range_minutes=(0,0),one_plot=False):
        """
        Plot trackers' Y positions. This function automatically adjusts the Y positions based on the experimental design file.

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        one_plot (bool): Flag to plot all trackers in one plot.
        """
        if(self.parameters.get_tracking_class() == Parameters.TrackingClass.COUNTING): 
            raise ValueError(f"Invalid tracking class: {self.parameters.get_tracking_class()}. Must be a Tracker.")
        
        if(one_plot):
            for treatment in self.experimental_design.tracking_regions['Treatment'].unique():
                fig, ax = plt.subplots(figsize=(10, 6))

                matching = [(key, t) for key, t in self.trackers.items()
                            if t.get_treatment() == treatment]
                ## plt.cm.get_cmap was removed in matplotlib 3.9.
                colormap = plt.get_cmap('tab10').resampled(max(len(matching), 1))

                for idx, (key, tracker) in enumerate(matching):
                    minutes = tracker.get_minutes(range_minutes)
                    y_positions = tracker.get_y_positions(range_minutes)
                    ax.plot(minutes, y_positions, label=key, color=colormap(idx),alpha=0.7)

                ax.set_xlabel('Minutes')
                ax.set_ylabel('Y Position')
                title = treatment + " (Axis flips applied if specified)"
                ax.set_title(title)            
                plt.show()            
        else:            
            for key, tracker in self.trackers.items():
                tracker.plot_y(range_minutes)
            
    def plot_trackers_xy(self,range_minutes=(0,0)):
        """
        Plot trackers' XY positions. This function automatically adjusts the X and Y positions based on the experimental design file.

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        for key, tracker in self.trackers.items():
                tracker.plot_xy(range_minutes)
     
    def plot_trackers_x_hist(self,bins=30,range_minutes=(0,0)):
        """
        Plot histogram of trackers' X positions. This function automatically adjusts the X positions based on the experimental design file.

        Parameters:
        bins (int): Number of bins for the histogram.
        range_minutes (tuple): Range of minutes to plot.
        """
        ## This method used to be defined twice with the parameters in opposite
        ## orders. The dead twin took range_minutes first, so a positional call
        ## carried over from an older notebook lands a tuple in `bins`, which
        ## matplotlib silently reads as bin *edges* and plots a one-bin histogram.
        if isinstance(bins, (tuple, list)):
            raise TypeError(
                "plot_trackers_x_hist(bins, range_minutes): `bins` must be an int. "
                f"Received {bins!r} — did you mean range_minutes={bins!r}?"
            )
        if(self.parameters.get_tracking_class() == Parameters.TrackingClass.COUNTING):
            raise ValueError(f"Invalid tracking class: {self.parameters.get_tracking_class()}. Must be a Tracker.")
        for key, tracker in self.trackers.items():
            tracker.plot_x_hist(bins=bins,range_minutes=range_minutes)
      
    def plot_trackers_time_dependent_interactions(self,window_size_min=10,step_size_min=5,range_minutes=(0,0)):
        """
        Plot time-dependent interactions of trackers.

        Parameters:
        window_size_min (int): Window size in minutes.
        step_size_min (int): Step size in minutes.
        range_minutes (tuple): Range of minutes to plot.
        """
        if((self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER) or (self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONCOUNTER)):
            last_region = None
            for key, tracker in self.trackers.items():
                if(tracker.tracking_region_id!=last_region):
                    tracker.plot_time_dependent_interactions(window_size_min,step_size_min,range_minutes)
                    last_region = tracker.tracking_region_id
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be a PairwiseInteractionTracker.")
   
    def plot_transitions(self,range_minutes=(0,0)):
        """
        Plot transitions.

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        if(self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICETRACKER):
            self.plot_transitions_twochoicetracker(range_minutes)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be a TwoChoiceTracker.")
  
    def plot_interactions(self,range_minutes=(0,0)):
        """
        Plot interactions.

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        if((self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER) or (self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONCOUNTER)):
            self.plot_interactions_pairwiseinteractiontracker(range_minutes)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be a TwoChoiceTracker.")
      
    def plot_interactions_facet(self,cutoffs=None):
        """
        Plot interactions in facets.

        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        if((self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER) or (self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONCOUNTER)):
            self.plot_interactions_facet_pairwiseinteractiontracker(cutoffs)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be a TwoChoiceTracker.")
        
    def plot_transitions_facet(self,cutoffs=None):
        """
        Plot transitions in facets.

        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        if(self.parameters.get_tracking_type()==Parameters.TrackingType.TWOCHOICETRACKER):
            self.plot_transitions_facet_twochoicetracker(cutoffs)
        else:
            raise ValueError(f"Invalid tracking type: {self.parameters.get_tracking_type()}. Must be a TwoChoiceTracker.")
    
#endregion ########### User Plotting Functions ############


#region ########### Backend Plotting ############
    def plot_pi_twochoicetracker(self, range_minutes=(0,0)):
        """
        Backend function to plot PI for TwoChoiceTracker.

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        summary_data = self.summarize(range_minutes)
        summary_data = self._abbrev_df(summary_data)
        plt.figure(figsize=(10, 6))
        p=sns.stripplot(x='Treatment', y='FinalPI', data=summary_data, jitter=True,  hue='Transitions')
        tmp = f"PI Range Minutes = [{summary_data["StartMinutes"].min():.2f} , {summary_data["EndMinutes"].max():.2f}]"
        plt.title(tmp)
        plt.xlabel('Treatment')
        plt.ylabel('PI')
        plt.ylim(-1.1,1.1)
        ntreatments = summary_data['Treatment'].nunique()
        plt.xlim(-.5,ntreatments-1+0.5)

        df_mean = summary_data.groupby('Treatment', sort=False)['FinalPI'].mean()
        ax = plt.gca()
        x_coords = ax.get_xticks()
        counter=0
        for i, y in df_mean.items():
            p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
            counter+=1

        tmp = list(self.experimental_design.counting_regions.keys())
        ax.text(-0.4,1,tmp[0])
        ax.text(-0.4,-1,tmp[1])
        plt.show()

    def plot_pi_twochoicecounter(self, range_minutes=(0,0)):
        """
        Backend function to plot PI for TwoChoiceTracker.

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        summary_data = self.summarize(range_minutes)
        summary_data = self._abbrev_df(summary_data)
        plt.figure(figsize=(10, 6))
        p=sns.stripplot(x='Treatment', y='FinalPI', data=summary_data, jitter=True)
        tmp = f"PI Range Minutes = [{summary_data["StartMinutes"].min():.2f} , {summary_data["EndMinutes"].max():.2f}]"
        plt.title(tmp)
        plt.xlabel('Treatment')
        plt.ylabel('PI')
        plt.ylim(-1.1,1.1)
        ntreatments = summary_data['Treatment'].nunique()
        plt.xlim(-.5,ntreatments-1+0.5)

        df_mean = summary_data.groupby('Treatment', sort=False)['FinalPI'].mean()
        ax = plt.gca()
        x_coords = ax.get_xticks()
        counter=0
        for i, y in df_mean.items():
            p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
            counter+=1

        tmp = list(self.experimental_design.counting_regions.keys())
        ax.text(-0.4,1,tmp[0])
        ax.text(-0.4,-1,tmp[1])
        plt.show()

    def plot_transitions_twochoicetracker(self, range_minutes=(0,0)):
        """
        Backend function to plot transitions for TwoChoiceTracker.
        Generally not called by the user.

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        summary_data = self.summarize(range_minutes)
        summary_data = self._abbrev_df(summary_data)
        plt.figure(figsize=(10, 6))
        p=sns.stripplot(x='Treatment', y='TransitionsPerMin', data=summary_data, jitter=True,  hue='FinalPI')
        tmp = f"Transitions Range Minutes = [{summary_data["StartMinutes"].min():.2f} , {summary_data["EndMinutes"].max():.2f}]" 
        plt.title(tmp)
        plt.xlabel('Treatment')
        plt.ylabel('Transitions (transitions/min)')        
        ntreatments = summary_data['Treatment'].nunique()
        plt.xlim(-.5,ntreatments-1+0.5)

        df_mean = summary_data.groupby('Treatment', sort=False)['TransitionsPerMin'].mean()
        ax = plt.gca()
        x_coords = ax.get_xticks()
        counter=0
        for i, y in df_mean.items():
            p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
            counter+=1
               
        plt.show()

    def plot_pi_facet_twochoicetracker(self, cutoffs=None):   
        """
        Backend function to plot PI in facets for TwoChoiceTracker.

        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        # Create a new column for the minute ranges
        the_data = self.summarize_facet(cutoffs)
        the_data = self._abbrev_df(the_data)

        def custom_plot(data, **kwargs):
            kwargs.pop('color', None)  # FacetGrid injects color; hue handles it.
            p=sns.stripplot(x='Treatment', y='FinalPI', hue='Transitions', data=data, jitter=True, **kwargs)
            means = data.groupby('Treatment', sort=False)['FinalPI'].mean()
            ax = plt.gca()
            x_coords = ax.get_xticks()
            counter=0
            for i, y in means.items():
                p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
                counter+=1
            ntreatments = data['Treatment'].nunique()
            tmp = list(self.experimental_design.counting_regions.keys())        
            ax.text(-0.4,1,tmp[0])
            ax.text(-0.4,-1,tmp[1])
            plt.xlim(-.5,ntreatments-1+0.5)

        # Create the FacetGrid
        g = self._facet_grid(the_data)
        #g.map(sns.stripplot, 'Treatment', 'FinalPI', 'Transitions', jitter=True)
        g.map_dataframe(custom_plot)
        # Add titles and labels
        g.set_titles(col_template="Facet Range (min): {col_name}")
        g.set_axis_labels('Treatment', 'PI')
        g.set(ylim=(-1.1, 1.1))
        
        plt.show()

    def plot_pi_facet_twochoicecounter(self, cutoffs=None):   
        """
        Backend function to plot PI in facets for TwoChoiceCounter.

        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        # Create a new column for the minute ranges
        the_data = self.summarize_facet(cutoffs)
        the_data = self._abbrev_df(the_data)

        def custom_plot(data, **kwargs):
            p=sns.stripplot(x='Treatment', y='FinalPI', data=data, jitter=True, **kwargs)
            means = data.groupby('Treatment', sort=False)['FinalPI'].mean()
            ax = plt.gca()
            x_coords = ax.get_xticks()
            counter=0
            for i, y in means.items():
                p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
                counter+=1
            ntreatments = data['Treatment'].nunique()
            tmp = list(self.experimental_design.counting_regions.keys())
            ax.text(-0.4,1,tmp[0])
            ax.text(-0.4,-1,tmp[1])
            plt.xlim(-.5,ntreatments-1+0.5)

        # Create the FacetGrid
        g = self._facet_grid(the_data)
        #g.map(sns.stripplot, 'Treatment', 'FinalPI', 'Transitions', jitter=True)
        g.map_dataframe(custom_plot)
        # Add titles and labels
        g.set_titles(col_template="Facet Range (min): {col_name}")
        g.set_axis_labels('Treatment', 'PI')
        g.set(ylim=(-1.1, 1.1))

        plt.show()


    def plot_transitions_facet_twochoicetracker(self, cutoffs=None):   
        """
        Backend function to plot transitions in facets for TwoChoiceTracker.
        Generally not called by the user.   

        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        # Create a new column for the minute ranges
        the_data = self.summarize_facet(cutoffs)
        the_data = self._abbrev_df(the_data)

        def custom_plot(data, **kwargs):
            kwargs.pop('color', None)  # FacetGrid injects color; hue handles it.
            p=sns.stripplot(x='Treatment', y='TransitionsPerMin', hue='FinalPI', data=data, jitter=True, **kwargs)
            means = data.groupby('Treatment', sort=False)['TransitionsPerMin'].mean()
            ax = plt.gca()
            x_coords = ax.get_xticks()
            counter=0
            for i, y in means.items():
                p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
                counter+=1
            ntreatments = data['Treatment'].nunique()
            plt.xlim(-.5,ntreatments-1+0.5)
            
        # Create the FacetGrid
        g = self._facet_grid(the_data, sharey=False)
        #g.map(sns.stripplot, 'Treatment', 'FinalPI', 'Transitions', jitter=True)
        g.map_dataframe(custom_plot)
        # Add titles and labels
        g.set_titles(col_template="Facet Range (min): {col_name}")
        g.set_axis_labels('Treatment', 'Transitions (transitions/min)')        
        
        plt.show()

    def plot_percentage_twochoicetracker(self, range_minutes=(0,0)):
        """
        Backend function to plot percentage for TwoChoiceTracker.

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        summary_data = self.summarize(range_minutes)
        summary_data = self._abbrev_df(summary_data)
        plt.figure(figsize=(10, 6))
        p=sns.stripplot(x='Treatment', y='FinalPercentage', data=summary_data, jitter=True,  hue='Transitions')
        tmp = f"Percentage Range Minutes = [{summary_data["StartMinutes"].min():.2f} , {summary_data["EndMinutes"].max():.2f}]" 
        plt.title(tmp)
        plt.xlabel('Treatment')
        plt.ylabel('Percentage')
        plt.ylim(-0.05,1.05)
        ntreatments = summary_data['Treatment'].nunique()
        plt.xlim(-.5,ntreatments-1+0.5)

        df_mean = summary_data.groupby('Treatment', sort=False)['FinalPercentage'].mean()
        ax = plt.gca()
        x_coords = ax.get_xticks()
        counter=0
        for i, y in df_mean.items():
            p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
            counter+=1
        tmp = list(self.experimental_design.counting_regions.keys())        
        ax.text(-0.4,1,tmp[0])
        ax.text(-0.4,-1,tmp[1])
        plt.show()

    def plot_percentage_twochoicecounter(self, range_minutes=(0,0)):
        """
        Backend function to plot percentage for TwoChoiceTracker.

        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        summary_data = self.summarize(range_minutes)
        summary_data = self._abbrev_df(summary_data)
        plt.figure(figsize=(10, 6))
        p=sns.stripplot(x='Treatment', y='FinalPercentage', data=summary_data, jitter=True)
        tmp = f"Percentage Range Minutes = [{summary_data["StartMinutes"].min():.2f} , {summary_data["EndMinutes"].max():.2f}]" 
        plt.title(tmp)
        plt.xlabel('Treatment')
        plt.ylabel('Percentage')
        plt.ylim(-0.05,1.05)
        ntreatments = summary_data['Treatment'].nunique()
        plt.xlim(-.5,ntreatments-1+0.5)

        df_mean = summary_data.groupby('Treatment', sort=False)['FinalPercentage'].mean()
        ax = plt.gca()
        x_coords = ax.get_xticks()
        counter=0
        for i, y in df_mean.items():
            p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
            counter+=1
        tmp = list(self.experimental_design.counting_regions.keys())        
        ax.text(-0.4,1,tmp[0])
        ax.text(-0.4,-1,tmp[1])
        plt.show()
    
    def plot_adj_x_pos_facet_xchoicetracker(self, cutoffs=None):
        """
        Backend function to plot adjusted X position in facets for XChoiceTracker.
        Generally not called by the user.

        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        # Create a new column for the minute ranges
        the_data = self.summarize_facet(cutoffs)
        the_data = self._abbrev_df(the_data)

        def custom_plot(data, **kwargs):
            kwargs.pop('color', None)  # FacetGrid injects color; hue handles it.
            p=sns.stripplot(x='Treatment', y='AvgAdjX_mm', hue='TotalXDistance_mm', data=data, jitter=True, **kwargs)
            means = data.groupby('Treatment', sort=False)['AvgAdjX_mm'].mean()
            ax = plt.gca()
            x_coords = ax.get_xticks()
            counter=0
            for i, y in means.items():
                p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
                counter+=1
            ntreatments = data['Treatment'].nunique()
            plt.xlim(-.5,ntreatments-1+0.5)

        # Create the FacetGrid
        g = self._facet_grid(the_data)
        #g.map(sns.stripplot, 'Treatment', 'FinalPI', 'Transitions', jitter=True)
        g.map_dataframe(custom_plot)
        # Add titles and labels
        g.set_titles(col_template="Facet Range (min): {col_name}")
        g.set_axis_labels('Treatment', 'Avg Adjusted X Position')
        
        plt.show()
         
    def plot_adj_x_pos_xchoicetracker(self, range_minutes=(0,0)):
        """
        Backend function to plot adjusted X position for XChoiceTracker.
        Generally not called by the user.
        
        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        summary_data = self.summarize(range_minutes)
        summary_data = self._abbrev_df(summary_data)
        plt.figure(figsize=(10, 6))
        p=sns.stripplot(x='Treatment', y='AvgAdjX_mm', data=summary_data, jitter=True,  hue='TotalXDistance_mm')
        tmp = f"Percentage Range Minutes = [{summary_data["StartMinutes"].min():.2f} , {summary_data["EndMinutes"].max():.2f}]" 
        plt.title(tmp)
        plt.xlabel('Treatment')
        plt.ylabel('Avg Adjusted X Position')
        ntreatments = summary_data['Treatment'].nunique()
        plt.xlim(-.5,ntreatments-1+0.5)

        df_mean = summary_data.groupby('Treatment', sort=False)['AvgAdjX_mm'].mean()
        ax = plt.gca()
        x_coords = ax.get_xticks()
        counter=0
        for i, y in df_mean.items():
            p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
            counter+=1
        plt.show()
        
    def plot_interactions_pairwiseinteractiontracker(self, range_minutes=(0,0)):
        """
        Backend function to plot interactions for PairwiseInteractionTracker.
        Generally not called by the user.
        
        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        summary_data = self.summarize(range_minutes, remove_partners=True)
        summary_data = self._abbrev_df(summary_data)
        print(summary_data )
        ## Remember we need to ensure only one of the pair is included.
        for dist_column in [f"PercentInteracting_{dist}" for dist in self.parameters.interaction_distance_mm]:
            plt.figure(figsize=(10, 6))
            p=sns.stripplot(x='Treatment', y=dist_column, data=summary_data, jitter=True)
            tmp = f"{dist_column} Range Minutes = [{summary_data["StartMinutes"].min():.2f} , {summary_data["EndMinutes"].max():.2f}]" 
            plt.title(tmp)
            plt.xlabel('Treatment')
            plt.ylabel('Fraction Frames Interacting')
            plt.ylim(-0.05,1.05)
            ntreatments = summary_data['Treatment'].nunique()
            plt.xlim(-.5,ntreatments-1+0.5)

            df_mean = summary_data.groupby('Treatment', sort=False)[dist_column].mean()
            ax = plt.gca()
            x_coords = ax.get_xticks()
            counter=0
            for i, y in df_mean.items():
                p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
                counter+=1
            plt.show()

    def plot_interactions_facet_pairwiseinteractiontracker(self, cutoffs=None):   
        """
        Backend function to plot interactions in facets for PairwiseInteractionTracker.
        Generally not called by the user.
        
        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        # Create a new column for the minute ranges
        the_data = self.summarize_facet(cutoffs,remove_partners=True)
        the_data = self._abbrev_df(the_data)
        def custom_plot(data, colname, **kwargs):
                p=sns.stripplot(x='Treatment', y=colname, data=data, jitter=True, **kwargs)
                means = data.groupby('Treatment', sort=False)[colname].mean()
                ax = plt.gca()
                x_coords = ax.get_xticks()
                counter=0
                for i, y in means.items():
                    p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
                    counter+=1
                ntreatments = data['Treatment'].nunique() 
                plt.title(colname)
                plt.xlim(-.5,ntreatments-1+0.5)
                
        for dist_column in [f"PercentInteracting_{dist}" for dist in self.parameters.interaction_distance_mm]:
            # Create the FacetGrid
            g = self._facet_grid(the_data)
            #g.map(sns.stripplot, 'Treatment', 'FinalPI', 'Transitions', jitter=True)
            g.map_dataframe(custom_plot,colname=dist_column)
            # Add titles and labels
            g.set_titles(col_template="Facet Range (min): {col_name}")
            g.set_axis_labels('Treatment', 'Fraction Frames Interacting')
            g.set(ylim=(-.05, 1.05))
            g.fig.suptitle(dist_column)
            plt.subplots_adjust(top=0.87)  # Adjust the top to make room for the title
            plt.show()

    def plot_percentage_facet_twochoicetracker(self, cutoffs=None):   
        """
        Backend function to plot percentage in facets for TwoChoiceTracker.
        Generally not called by the user.
        
        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        # Create a new column for the minute ranges
        the_data = self.summarize_facet(cutoffs)
        the_data = self._abbrev_df(the_data)

        def custom_plot(data, **kwargs):
            kwargs.pop('color', None)  # FacetGrid injects color; hue handles it.
            p=sns.stripplot(x='Treatment', y='FinalPercentage', hue='Transitions', data=data, jitter=True, **kwargs)
            means = data.groupby('Treatment', sort=False)['FinalPercentage'].mean()
            ax = plt.gca()
            x_coords = ax.get_xticks()
            counter=0
            for i, y in means.items():
                p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
                counter+=1
            ntreatments = data['Treatment'].nunique()
            tmp = list(self.experimental_design.counting_regions.keys())        
            ax.text(-0.4,1,tmp[0])
            ax.text(-0.4,0,tmp[1])
            plt.xlim(-.5,ntreatments-1+0.5)

        # Create the FacetGrid
        g = self._facet_grid(the_data)
        #g.map(sns.stripplot, 'Treatment', 'FinalPI', 'Transitions', jitter=True)
        g.map_dataframe(custom_plot)
        # Add titles and labels
        g.set_titles(col_template="Facet Range (min): {col_name}")
        g.set_axis_labels('Treatment', 'Percentage')
        g.set(ylim=(-.05, 1.05))
        
        plt.show()

    def plot_percentage_facet_twochoicecounter(self, cutoffs=None):   
        """
        Backend function to plot percentage in facets for TwoChoiceTracker.
        Generally not called by the user.
        
        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        # Create a new column for the minute ranges
        the_data = self.summarize_facet(cutoffs)
        the_data = self._abbrev_df(the_data)

        def custom_plot(data, **kwargs):
            p=sns.stripplot(x='Treatment', y='FinalPercentage', data=data, jitter=True, **kwargs)
            means = data.groupby('Treatment', sort=False)['FinalPercentage'].mean()
            ax = plt.gca()
            x_coords = ax.get_xticks()
            counter=0
            for i, y in means.items():
                p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
                counter+=1
            ntreatments = data['Treatment'].nunique()
            tmp = list(self.experimental_design.counting_regions.keys())        
            ax.text(-0.4,1,tmp[0])
            ax.text(-0.4,0,tmp[1])
            plt.xlim(-.5,ntreatments-1+0.5)

        # Create the FacetGrid
        g = self._facet_grid(the_data)
        #g.map(sns.stripplot, 'Treatment', 'FinalPI', 'Transitions', jitter=True)
        g.map_dataframe(custom_plot)
        # Add titles and labels
        g.set_titles(col_template="Facet Range (min): {col_name}")
        g.set_axis_labels('Treatment', 'Percentage')
        g.set(ylim=(-.05, 1.05))
        
        plt.show()


    def plot_totaldistance_generaltracker(self, range_minutes=(0,0)):
        """
        Backend function to plot total distance for general tracker.
        Generally not called by the user.
        
        Parameters:
        range_minutes (tuple): Range of minutes to plot.
        """
        summary_data = self.summarize(range_minutes)
        summary_data = self._abbrev_df(summary_data)
        plt.figure(figsize=(10, 6))
        p=sns.stripplot(x='Treatment', y='TotalDistancePerMin', data=summary_data, jitter=True)
        tmp = f"Distance Range Minutes = [{summary_data["StartMinutes"].min():.2f} , {summary_data["EndMinutes"].max():.2f}]" 
        plt.title(tmp)
        plt.xlabel('Treatment')
        plt.ylabel('Distance (mm/min)')
        ntreatments = summary_data['Treatment'].nunique()
        plt.xlim(-.5,ntreatments-1+0.5)

        df_mean = summary_data.groupby('Treatment', sort=False)['TotalDistancePerMin'].mean()
        ax = plt.gca()
        x_coords = ax.get_xticks()
        counter=0
        for i, y in df_mean.items():
            p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
            counter+=1
        tmp = list(self.experimental_design.counting_regions.keys())             
        plt.show()

    def plot_totaldistance_facet_generaltracker(self, cutoffs=None):   
        """
        Backend function to plot total distance in facets for general tracker.
        Generally not called by the user.
        
        Parameters:
        cutoffs (tuple): Cutoff values for facets.
        """
        # Create a new column for the minute ranges
        the_data = self.summarize_facet(cutoffs)
        the_data = self._abbrev_df(the_data)

        def custom_plot(data, **kwargs):
            p=sns.stripplot(x='Treatment', y='TotalDistancePerMin',data=data, jitter=True, **kwargs)
            means = data.groupby('Treatment', sort=False)['TotalDistancePerMin'].mean()
            ax = plt.gca()
            x_coords = ax.get_xticks()
            counter=0
            for i, y in means.items():
                p.hlines(y, x_coords[counter] - 0.05, x_coords[counter] + 0.05, color='red', zorder=2)
                counter+=1
            ntreatments = data['Treatment'].nunique()
            plt.xlim(-.5,ntreatments-1+0.5)

        # Create the FacetGrid
        g = self._facet_grid(the_data, sharey=False)
        #g.map(sns.stripplot, 'Treatment', 'FinalPI', 'Transitions', jitter=True)
        g.map_dataframe(custom_plot)
        # Add titles and labels
        g.set_titles(col_template="Facet Range (min): {col_name}")
        g.set_axis_labels('Treatment', 'Distance (mm/min)')        
        
        plt.show()

#endregion ########### Backend Plotting ############

#region ########### Special Analysis Functions ############

    def analyze_rle_data(self, change_none_to_light=True, min_duration_frames=1, range_minutes=(0, 0)):
        """
        Analyze RLE (Run Length Encoding) of choice behavior for all TwoChoiceTrackers.

        Parameters:
        change_none_to_light (bool): Replace 'None' region values with the first counting region name.
        min_duration_frames (int): Minimum run length in frames; shorter runs are removed.
        range_minutes (tuple): Time window to analyze; (0, 0) means all data.

        Returns:
        DataFrame: Per-tracker RLE statistics including mean and median run lengths per choice.
        """
        from .Parameters import TrackingType
        if self.parameters.get_tracking_type() != TrackingType.TWOCHOICETRACKER:
            raise ValueError(
                f"Invalid tracking type: {self.parameters.get_tracking_type()}. "
                "analyze_rle_data requires TwoChoiceTracker."
            )

        results = []
        for key, tracker in self.trackers.items():
            rle_data = tracker.rle(range_minutes)

            counting_names = list(tracker.counting_regions_design.keys()) if tracker.counting_regions_design else ["Treatment1", "Treatment2"]
            t1_name = counting_names[0] if len(counting_names) > 0 else "Treatment1"
            t2_name = counting_names[1] if len(counting_names) > 1 else "Treatment2"

            ## rle() returns the raw CountingRegion cells, which are region
            ## *aliases* ("L", "LL"), not the group keys they are declared
            ## under. Comparing them against group names directly never matches
            ## for any lab using the alias feature, so map first and compare on
            ## the group. Out-of-region runs map to NaN.
            region_to_group = ExperimentalDesign.alias_to_group_map(tracker.counting_regions_design)
            rle_data = rle_data.copy()
            rle_data['group'] = rle_data['values'].map(region_to_group)
            ## Re-encode on the group: consecutive runs under two aliases of the
            ## same group ("L" then "LL") are one visit, not two.
            rle_data = _merge_adjacent_runs(rle_data)

            if change_none_to_light:
                rle_data['group'] = rle_data['group'].fillna(t1_name)
                rle_data = _merge_adjacent_runs(rle_data)

            if min_duration_frames > 1:
                rle_data = rle_data[rle_data['lengths'] >= min_duration_frames].reset_index(drop=True)
                if len(rle_data) > 0:
                    rle_data = _merge_adjacent_runs(rle_data)

            treatment = tracker.tracking_region_design['Treatment'].iloc[0] if tracker.tracking_region_design is not None else "Unknown"

            rle_filtered = rle_data[rle_data['group'].notna()]
            t1_data = rle_filtered[rle_filtered['group'] == t1_name]
            t2_data = rle_filtered[rle_filtered['group'] == t2_name]

            results.append({
                'Tracker': key,
                'Treatment': treatment,
                'Treatment1_Name': t1_name,
                'Treatment2_Name': t2_name,
                'Treatment1_Rows': len(t1_data),
                'Treatment2_Rows': len(t2_data),
                'Treatment1_MeanLength': t1_data['lengths'].mean() if len(t1_data) > 0 else 0,
                'Treatment2_MeanLength': t2_data['lengths'].mean() if len(t2_data) > 0 else 0,
                'Treatment1_MedianLength': t1_data['lengths'].median() if len(t1_data) > 0 else 0,
                'Treatment2_MedianLength': t2_data['lengths'].median() if len(t2_data) > 0 else 0,
                'Treatment1_Lengths': t1_data['lengths'].tolist(),
                'Treatment2_Lengths': t2_data['lengths'].tolist(),
            })

        return pd.DataFrame(results)

    def analyze_rle_data_facet(self, cutoffs=None, change_none_to_light=True,
                               min_duration_frames=1, write_to_csvfile=False):
        """
        Faceted version of analyze_rle_data across time windows.

        Parameters:
        cutoffs (tuple|int): Facet boundary minutes.
        write_to_csvfile (bool): If True, saves results to the data directory.

        Returns:
        DataFrame: RLE statistics with a FacetRange column.
        """
        all_results = self._over_facets(
            self.analyze_rle_data, cutoffs,
            change_none_to_light=change_none_to_light,
            min_duration_frames=min_duration_frames,
        )
        if write_to_csvfile:
            path = self._output_path("_EventDuration_Summary_Facet.csv")
            all_results.to_csv(path, index=False)
        return all_results

    def analyze_distance_by_light(self, range_minutes=(0, 0)):
        """
        Analyze distance moved and time spent in each counting region (e.g. Light vs NoLight).

        For each tracker the function sums Dist_mm and DeltaSec within each group of counting
        regions as defined by the experimental design, then computes mm/sec speed for each group.

        Parameters:
        range_minutes (tuple): Time window; (0, 0) means all data.

        Returns:
        DataFrame: Per-tracker distance and time totals for each counting region group.
        """
        results = []
        for key, tracker in self.trackers.items():
            data_subset = tracker.get_data_subset(range_minutes)
            treatment = tracker.tracking_region_design['Treatment'].iloc[0] if tracker.tracking_region_design is not None else "Unknown"

            if tracker.counting_regions_design is None:
                continue
            if 'CountingRegion' not in data_subset.columns:
                continue

            group_names = list(tracker.counting_regions_design.keys())
            # Heuristic: first group matching "light" (and not "no") is Light; next is NoLight.
            light_group, nolight_group = None, None
            for g in group_names:
                g_lower = g.lower()
                if 'light' in g_lower and 'no' not in g_lower:
                    light_group = g
                elif 'no' in g_lower and 'light' in g_lower:
                    nolight_group = g
            if light_group is None or nolight_group is None:
                if len(group_names) >= 2:
                    light_group, nolight_group = group_names[0], group_names[1]
                else:
                    continue

            light_vals = tracker.counting_regions_design[light_group]
            nolight_vals = tracker.counting_regions_design[nolight_group]

            light_data = data_subset[data_subset['CountingRegion'].isin(light_vals)]
            nolight_data = data_subset[data_subset['CountingRegion'].isin(nolight_vals)]

            light_dist = light_data['Dist_mm'].sum() if 'Dist_mm' in light_data.columns else 0
            nolight_dist = nolight_data['Dist_mm'].sum() if 'Dist_mm' in nolight_data.columns else 0
            light_time = light_data['DeltaSec'].sum() if 'DeltaSec' in light_data.columns else 0
            nolight_time = nolight_data['DeltaSec'].sum() if 'DeltaSec' in nolight_data.columns else 0

            row = {
                'Tracker': key,
                'Treatment': treatment,
                f'{light_group}_Distance_mm': light_dist,
                f'{nolight_group}_Distance_mm': nolight_dist,
                f'{light_group}_Time_sec': light_time,
                f'{nolight_group}_Time_sec': nolight_time,
            }
            ## Plain floats, not np.where: with scalar arguments np.where returns
            ## a 0-d ndarray, which makes the whole column object dtype. That
            ## defeats pandas' skipna, so a single tracker with no time in one
            ## region turned that entire treatment's mean into NaN, and the
            ## exported CSV could not be re-read numerically.
            row[f'{light_group}_Distance_mm_sec'] = float(light_dist / light_time) if light_time > 0 else np.nan
            row[f'{nolight_group}_Distance_mm_sec'] = float(nolight_dist / nolight_time) if nolight_time > 0 else np.nan
            results.append(row)

        return pd.DataFrame(results)

    def analyze_distance_by_light_facet(self, cutoffs=None, copy_to_clipboard=False,
                                        write_to_csvfile=False):
        """
        Faceted version of analyze_distance_by_light across time windows.

        Parameters:
        cutoffs (tuple|int): Facet boundary minutes.
        copy_to_clipboard (bool): Copy result to clipboard.
        write_to_csvfile (bool): Save result to the data directory.

        Returns:
        DataFrame: Distance/time statistics with a FacetRange column.
        """
        all_results = self._over_facets(self.analyze_distance_by_light, cutoffs)
        if copy_to_clipboard:
            all_results.to_clipboard(index=False)
        if write_to_csvfile:
            path = self._output_path("_DistanceByLight_Facet.csv")
            all_results.to_csv(path, index=False)
        return all_results

#endregion ########### Special Analysis Functions ############

#region ########### Statistical Functions ############
    @staticmethod
    def _treatment_arms(summary):
        """Split a summary into (assigned rows, level names, count of unassigned rows).

        A tracking region whose ``experimental_factors`` is blank is *not* a
        treatment arm. Carrying it through as one silently converts a two-arm
        t-test into a three-arm Tukey HSD, inflates the multiplicity family and
        reports a nameless n=1 group as a result. One blank region in the config
        is enough to trigger it, so exclude and announce rather than analyse.
        """
        labels = summary['Treatment'].astype(str).str.strip()
        assigned = labels.ne('') & labels.str.lower().ne('nan')
        return summary[assigned], list(pd.unique(labels[assigned])), int((~assigned).sum())

    @staticmethod
    def _print_group_comparison(subset, metric, treatments, window_label, equal_var=False):
        """Print one two-group comparison, including each group's N.

        The N matters: ``pd.to_numeric(..., errors='coerce').dropna()`` silently
        discards trackers with no numeric value for the metric — routine for
        ``FinalPI`` and ``Transitions`` in early facets, where an animal may
        never enter a counting region. Without N a reader of ``*_Stats.txt``
        cannot tell whether a p-value rests on 18 animals or 8.
        """
        labels = subset['Treatment'].astype(str).str.strip()
        numeric = pd.to_numeric(subset[metric], errors='coerce')
        group1 = numeric[labels == treatments[0]].dropna()
        group2 = numeric[labels == treatments[1]].dropna()
        if len(group1) < 2 or len(group2) < 2:
            print(f"Warning: Insufficient numeric data for {metric} in {window_label} "
                  f"(n={len(group1)}, n={len(group2)}). Skipping.")
            return False

        t_stat, p_value = ttest_ind(group1, group2, equal_var=equal_var)
        test_name = "Student's t-test (equal variance)" if equal_var else "Welch's t-test (unequal variance)"
        print("############# T-Test #############")
        print(f"Column = {metric}, Range Minutes = {window_label}")
        print(f"{treatments[0]} (n={len(group1)}, mean={group1.mean():.4g}, sd={group1.std(ddof=1):.4g}) vs. "
              f"{treatments[1]} (n={len(group2)}, mean={group2.mean():.4g}, sd={group2.std(ddof=1):.4g}): "
              f"T={t_stat:.2f}, p={p_value:.5f}")
        print(f"Test: {test_name}")
        dropped = len(subset) - len(group1) - len(group2)
        if dropped:
            print(f"  note: {dropped} tracker(s) excluded — no numeric {metric} in this window.")
        return True

    def run_pairwise_comparisons(self, metric='FinalPI', range_minutes=(0,0), equal_var=False):
        """
        Run pairwise comparisons for a given metric.
        Generally not called by the user.

        Parameters:
        metric (str): Metric to compare.
        range_minutes (tuple): Range of minutes to compare.
        equal_var (bool): ``False`` (default) runs Welch's t-test, which does not
            assume equal variances and is the safer choice for unbalanced groups.
            Pass ``True`` for the classic Student's t-test.

        Returns:
            ``"Not applicable"`` if there are fewer than two treatment levels,
            otherwise ``None`` (results are printed).
        """
        remove_partners = False
        if((self.parameters.get_tracking_type()==Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER) and ("Interacting" in metric)):
            remove_partners=True
        summary = self.summarize(range_minutes, remove_partners=remove_partners)
        if 'Treatment' not in summary.columns:
            raise ValueError("The summary data does not contain a 'Treatment' column.")
        if metric not in summary.columns:
            raise ValueError(f"The summary data does not contain a '{metric}' column.")

        summary, treatments, unassigned = self._treatment_arms(summary)
        if unassigned:
            print(f"Warning: {unassigned} tracker(s) have no experimental_factors assigned "
                  f"and were excluded from the {metric} comparison. Check tracking_config.yaml.")

        window_label = f"({range_minutes[0]:.2f} , {range_minutes[1]:.2f})"

        if(len(treatments)<2):
            msg = f"Not applicable: {metric} has fewer than two treatment levels."
            print(msg)
            return msg
        elif(len(treatments)==2):
            self._print_group_comparison(summary, metric, treatments, window_label, equal_var=equal_var)
        else:
            metric_numeric = pd.to_numeric(summary[metric], errors='coerce')
            valid_mask = metric_numeric.notna()
            if valid_mask.sum() == 0:
                print(f"Warning: No valid numeric data for {metric} comparison. Skipping.")
                return
            tukey = pairwise_tukeyhsd(endog=metric_numeric[valid_mask], groups=summary['Treatment'][valid_mask], alpha=0.05)
            print(f"Column = {metric}, Range Minutes = [{range_minutes[0]:.2f} , {range_minutes[1]:.2f}] ")
            print(f"Group sizes: " + ", ".join(
                f"{level} n={int((summary['Treatment'][valid_mask] == level).sum())}" for level in treatments))
            print(tukey)

    def run_pairwise_comparisons_facet(self, metric='FinalPI', cutoffs=None, remove_partners=False,
                                       equal_var=False):
        """
        Run pairwise comparisons in facets for a given metric.
        Generally not called by the user.

        Parameters:
        metric (str): Metric to compare.
        cutoffs (tuple): Cutoff values for facets.
        remove_partners (bool): Flag to remove partners from the comparison.
        equal_var (bool): ``False`` (default) runs Welch's t-test. See
            :meth:`run_pairwise_comparisons`.
        """
        summary = self.summarize_facet(cutoffs, remove_partners=remove_partners)
        if 'Treatment' not in summary.columns:
            raise ValueError("The summary data does not contain a 'Treatment' column.")
        if metric not in summary.columns:
            raise ValueError(f"The summary data does not contain a '{metric}' column.")

        summary, _, unassigned = self._treatment_arms(summary)
        if unassigned:
            print(f"Warning: {unassigned} tracker-facet row(s) have no experimental_factors assigned "
                  f"and were excluded from the {metric} comparison. Check tracking_config.yaml.")

        applicable = False
        tests_run = 0
        for frange in summary['FacetRange'].unique():
            subset = summary[summary['FacetRange'] == frange]
            treatments = list(pd.unique(subset['Treatment'].astype(str).str.strip()))
            if(len(treatments)<2):
                print(f"Not applicable for facet {frange}: fewer than two treatment levels.")
                continue
            applicable = True
            window_label = f"({frange[0]:.2f} , {frange[1]:.2f})"
            if(len(treatments)==2):
                if self._print_group_comparison(subset, metric, treatments, window_label, equal_var=equal_var):
                    tests_run += 1
                print("\n")
            else:
                metric_numeric = pd.to_numeric(subset[metric], errors='coerce')
                valid_mask = metric_numeric.notna()
                if valid_mask.sum() == 0:
                    print(f"Warning: No valid numeric data for {metric} in facet {frange}. Skipping.")
                    print("\n")
                    continue
                tukey = pairwise_tukeyhsd(endog=metric_numeric[valid_mask], groups=subset['Treatment'][valid_mask], alpha=0.05)
                print(f"Column = {metric}, Facet Range = ({frange[0]:.2f},{frange[1]:.2f})")
                print(tukey)
                print("\n")

        if tests_run > 1:
            ## Each facet is a separate test of the same hypothesis. Nothing here
            ## corrects for that, so say so rather than let the reader assume the
            ## printed p-values are family-wise.
            print(f"Note: {tests_run} independent t-tests were run for {metric} across facets. "
                  f"No multiplicity correction is applied; at alpha=0.05 a Bonferroni-adjusted "
                  f"threshold would be {0.05 / tests_run:.5f}.")
            print("\n")

        if not applicable:
            return f"Not applicable: {metric} has fewer than two treatment levels in any facet."

#endregion ########### Statistical Functions ############

#region ########### QC Functions ############


#endregion ########### QC Functions ############    
if __name__ == "__main__":
    """
    Main function to test the Arena class.
    """
    p=Parameters.Parameters()
    #p.set_small_arena_values(Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER)
    #p.set_pairwise_interactioncounter_values_arena_max([2,4,8])
    #p.set_movie_values(Parameters.TrackingType.TWOCHOICETRACKER, 10, 0.056)
    p.set_colloseum_values(Parameters.TrackingType.TWOCHOICETRACKER)         
    arena = Arena(p,data_path="./Data/")
    arena.delete_tracker("T_23","0")
    arena.print_short_data_quality_report()
    #print(arena.get_data_quality())
    #print(arena.first_tracker().rawdata[['ClosestNeighbor','IsNeighborValid']].head(30))
    #arena.first_tracker().plot_cumulative_percentage(range_minutes=(0,0),show_light=True)
    #print(arena.summarize_facet(cutoffs=(10,15)))
    #arena.run_pairwise_comparisons_facet(metric="VarAdjX_mm",cutoffs=(10,15))
    #arena.plot_adjusted_x_position_facet(cutoffs=(10,15))
    #arena.plot_trackers_x()
    #arena.summarize_facet(cutoffs=(10,70),remove_partners=True)
    #arena.plot_trackers_time_dependent_interactions()
    #print(arena.first_tracker().plot_time_dependent_distances())
    #arena.run_pairwise_comparisons_facet(cutoffs=(10,70))
    #arena.plot_percentage_facet(cutoffs=(10,70))
    #arena.plot_pi_facet((10,70))
    #print(arena.plot_percentage_facet())
    #print(arena.summarize(write_to_csvfile=True))   
    #print(arena.summarize_facet(cutoffs=(10)))
    #arena.plot_trackers_y(range_minutes=(0,0),one_plot=True)
        
    #print(arena.get_tracker("R1_0").plot_xy_animated(range_minutes=(10,15),tail_size=1000))

    #arena.get_tracker("R1_1").plot_time_dependent_interactions()
    #arena.get_tracker("R1_1").plot_x()
    #arena.plot_pi(range_minutes=(0,0))
    #arena.plot_transitions_facet()
    #arena.get_tracker("T_1_0").get_x_positions((10,20))
    #print(arena.firstTracker().tracking_region)
    #print(arena.firstTracker().counting_regions)
    #print(arena.first_tracker().PlotXY())
    #print(arena.firstTracker().PlotX())
    #print(arena.firstTracker().PlotY())
    #arena.firstTracker().summarize()
    #arena.test()
