from src.datasets.ct.lndb_utils import *
import numpy as np
import copy
from matplotlib import pyplot as plt
import pandas as pd
import os
import sys
from typing import Any
from PIL import Image
from torch.utils.data import Dataset
from torchvision import transforms
from torchvision.datasets.utils import extract_archive
from src.datasets.specs import Input3dSpec
import torch
from src.datasets.ct.readNoduleList import nodEqDiam, get_merged_nodules
import ast
import tqdm
from collections import defaultdict
from scipy.ndimage import zoom
#import cv2

LNDB_NUM_WINDOW_COL = 'num_windows'
LNDB_STUDY_COL = 'LNDbID'
LNDB_NUM_SLICES_COL = 'total_scans'
LNDB_WINDOWS_CSV = 'LNDb_windows.csv'
LNDB_WINDOW_INDEX_COL = 'window_idx'
LNDB_WINDOW_LABEL_COL = 'window_lbl'
LNDB_DATA_DIR = ''
LNDB_ORIGINAL_CSV = 'trainNodules.csv'
LNDB_IMG_DIR = 'data0/'
LNDB_INSTANCE_ORDER_COL = 'instance_order'
LNDB_MIN_AGREEMENT = 1
LNDB_MIN_ABNORMAL_SLICE = 2
LNDB_DATA_DIR = ''
LNDB_IMAGE_SIZE = 256
LNDB_WINDOW_SIZE = 24
LNDB_INPUT_SIZE = (LNDB_IMAGE_SIZE, LNDB_IMAGE_SIZE, LNDB_WINDOW_SIZE)
LNDB_PATCH_SIZE = (16, 16, LNDB_WINDOW_SIZE)
LNDB_SERIES_CSV = 'nodules_with_coords.csv'
NODULE_WINDOW_CENTER = -600
NODULE_WINDOW_WIDTH = 1500
def any_exist(files):
    return any(map(os.path.exists, files))

def all_exist(files):
    return all(map(os.path.exists, files))


class LNDb(Dataset):
    """A dataset class for the LNDb dataset: The LNDb dataset contains 294 CT scans collected retrospectively 
    at the Centro Hospitalar e Universitário de São João (CHUSJ) in Porto, Portugal between 2016 and 2018.
    Each CT scan was read by at least one radiologist at CHUSJ to identify pulmonary nodules and other suspicious lesions.
    A total of 5 radiologists with at least 4 years of experience reading up to 30 CTs per week participated in the annotation process throughout the project.
    Annotations were performed in a single blinded fashion, i.e. a radiologist would read the scan once and no consensus or review between the radiologists was performed. 
    Each scan was read by at least one radiologist. The instructions for manual annotation were adapted from LIDC-IDRI. Each radiologist identified the following lesions:
    
    nodule ⩾3mm: any lesion considered to be a nodule by the radiologist with greatest in-plane dimension larger or equal to 3mm;
    
    nodule <3mm: any lesion considered to be a nodule by the radiologist with greatest in-plane dimension smaller than 3mm;
    non-nodule: any pulmonary lesion considered not to be a nodule by the radiologist, but that contains features which could make it identifiable as a nodule;
    """

    INPUT_SIZE = LNDB_INPUT_SIZE 
    PATCH_SIZE = LNDB_PATCH_SIZE 
    IN_CHANNELS = 1

    def __init__(
        self, base_root: str, download: bool = True, train: bool = True
    ):

        # check if LNDb is downloaded and pre-processed
        self.root = os.path.join(base_root, 'CT2', 'LNDb')
        
        self.check_data()

        self.df = pd.read_csv(
            os.path.join(self.root,LNDB_WINDOWS_CSV),
        )


        super().__init__()

    def check_data(self):
        
        orig_csv =  os.path.join(self.root, LNDB_ORIGINAL_CSV)
        img_dir = os.path.join(self.root, LNDB_IMG_DIR)
        window_csv = os.path.join(self.root, LNDB_WINDOWS_CSV)

        # if no data is present, prompt the user to download it
        if not any_exist([orig_csv] + [img_dir]):
            raise RuntimeError(
                f"""
                Visit XXXX to download the LNDb dataset
                Once the download completes, place the rar files in {LNDB_DATA_DIR} and unzip the file
                """
            )

        # if the data has not been extracted, extract the data, prioritizing the full-res dataset
        if not all_exist([window_csv]):
            print("Pre-processing LNDb...")
            self.preprocess_data()
            print("LNDb pre-processing complete!")

    def preprocess_data(self):
        merged_nodules = get_merged_nodules(self.root)
        if not os.path.exists(os.path.join(self.root, LNDB_SERIES_CSV)):
            self.gen_series_df(merged_nodules,LNDB_MIN_AGREEMENT)
        print('process window')
        series_df = pd.read_csv(os.path.join(self.root, LNDB_SERIES_CSV))
        print(type(series_df))
        df_windows = self.process_window_df(series_df)
        df_windows.to_csv(os.path.join(self.root, LNDB_WINDOWS_CSV))
        
  
    def __getitem__(self, index):

        row = self.df.iloc[index]
        study_name = row[LNDB_STUDY_COL]
        slice_idx = sorted(ast.literal_eval(row[LNDB_INSTANCE_ORDER_COL]))

        # extract and transform window
        [scan,spacing,origin,transfmat] =  readMhd(os.path.join(self.root,'data0/LNDb-{:04}.mhd'.format(study_name)))
        window = scan[slice_idx]
        
        #Resize windows
        sz,sx,sy = window.shape
        window = zoom(window, (1, LNDB_IMAGE_SIZE/sx, LNDB_IMAGE_SIZE/sy))
        
        #Put 
        window = self.windowing(window, NODULE_WINDOW_CENTER, NODULE_WINDOW_WIDTH)

        if len(window.shape) == 3:
            window = np.expand_dims(window, 1)
        
        x = torch.from_numpy(window).float()
    
        x = x.permute((1, 2, 3, 0))
        x = x.type(torch.FloatTensor)

        if self.IN_CHANNELS == 3:
            c, w, h, d = x.shape
            x = x.expand(3, w, h, d)
 
        # get labels
        y = np.array(ast.literal_eval(row[LNDB_WINDOW_LABEL_COL]))
        y = torch.tensor(y).type(torch.LongTensor)

        return index, x, y

    def __len__(self):
        return len(self.df)

    @staticmethod
    def num_classes():
        """Predicting Non-Nodule Nodule<3mm Nodule>3mm"""
        return 2

    def windowing(self, pixel_array: np.array, window_center: int, window_width: int):
        """
        Adjust pixel array of CT scans to a particular viewing window. The
        windowing opertaion will change the appearance of the image to highlight
        particular anatomical structures.
        The upperbound of the viewing window is calcuated as:
            WindowCetner + WindowWidth // 2
        While the lowerbound of the viewing window is:
            WindowCetner - WindowWidth // 2
        More information can be found here:
            XXXX
        Args:
            pixel_array (np.array): Pixel array of a CT scan.
            window_center (int): Midpoint of the range of the CT numbers displayed.
            window_width (int): The range of CT numbers that an image contains
        """

        lower = window_center - window_width // 2
        upper = window_center + window_width // 2
        pixel_array = np.clip(pixel_array.copy(), lower, upper)
        pixel_array = (pixel_array - lower) / (upper - lower)

        return pixel_array
    def process_window_df(
            self,
            df_coords: pd.DataFrame,
            num_slices: int = LNDB_WINDOW_SIZE,
            min_abnormal_slice: int = LNDB_MIN_ABNORMAL_SLICE,
            stride: int = 12,
        ):
            f"""
            Convert each CT series to sliding windows of N slices. The processed window
            level information are stored in {LNDB_WINDOWS_CSV}.
            The number of windows for a series is calculated by:
                (num_windows - num_slices) // stride
            Args:
                df_coords (pd.DataFrame): DataFrame with slice level information 
                num_slices (int); number of slices per window
                min_abnormal_slice (int): number of abnormal slices needed to consider a
                    window as abnormal
                stride (int): spacing between each window
            Returns:
                A DataFrame with processed window level information.
            """

            # count number of windows per series
            count_num_windows = lambda x: (x - num_slices) // stride
            print(type(df_coords))
            df_series=df_coords.copy().groupby(LNDB_STUDY_COL).head(1).reset_index()
           # print(df_series.isna().sum())
            df_series[LNDB_NUM_WINDOW_COL] = df_series[LNDB_NUM_SLICES_COL].apply(count_num_windows)

            # generate windows list
            window_labels = defaultdict(list)

            # studies
            for _, row in tqdm.tqdm(df_series.iterrows(), total=df_series.shape[0]):
                study_name = row[LNDB_STUDY_COL]
            #    split = row[RADFUSION_SPLIT_COL]

                study_df = df_coords[df_coords[LNDB_STUDY_COL] == study_name]
                # windows
                for idx in range(int(row[LNDB_NUM_WINDOW_COL])):
                    start_idx = idx * stride
                    end_idx = min((idx * stride) + num_slices, row[LNDB_NUM_SLICES_COL])
                    big_nodule_count = 0
                    small_nodule_count = 0
                    for _, study_row in study_df.iterrows():
                        diameter = nodEqDiam(study_row['Volume'])
                        nod = study_row['Nodule']
                        if nod == 1:
                            r1,r2 = tuple(ast.literal_eval(study_row['coords']))
                            x = set(range(start_idx, end_idx+1))
                            y = range(r1,r2+1)
                            overlap = len(x.intersection(y))
                            if diameter <= 3:
                                small_nodule_count += overlap
                            else:
                                big_nodule_count += overlap
#                     labels = {min_abnormal_slice:0,small_nodule_count:1,big_nodule_count:2}
#                     label = labels.get(max(labels))
                    y = list(np.zeros(2))
    
                    if small_nodule_count >= min_abnormal_slice:
                        y[0] =1
                    if big_nodule_count >= min_abnormal_slice:
                        y[1] =1 

                    window_labels[LNDB_STUDY_COL].append(study_name)
                   # window_labels[LNDB_SPLIT_COL].append(split)
                    window_labels[LNDB_WINDOW_INDEX_COL].append(idx)
                    window_labels[LNDB_WINDOW_LABEL_COL].append(str(y))
                    window_labels[LNDB_INSTANCE_ORDER_COL].append(
                        np.arange(start_idx, end_idx).tolist()
                    )
            df_out = pd.DataFrame.from_dict(window_labels) 

            return df_out
    
    
    
    def gen_series_df(self, csv, min_agreement):

        def find_first(mask):
            for i in range(mask.shape[0]):
                if mask[i].sum()>0:
                    return i
            return -1

        def find_last(mask):
            r=mask.shape[0]-1
            for i in range(r):
                if mask[r-i].sum()>0:
                    return r-i
            return -1
        # Read nodules csv
        csvlines = readCsv(csv)
        header = csvlines[0]
        nodules = csvlines[1:]
        df = pd.read_csv(csv)
        df['total_scans'] = None
        df['coords'] = None
        lndloaded = -1
        rownum = -1
        min_agreement = min_agreement
        for n in nodules:
                rownum+=1
                vol = float(n[header.index('Volume')])
                is_nol = int(n[header.index('Nodule')])
                agr_lvl = int (n[header.index('AgrLevel')])
                ctr = np.array([float(n[header.index('x')]), float(n[header.index('y')]), float(n[header.index('z')])])
                lnd = int(n[header.index('LNDbID')])
                rads = list(map(int,list(n[header.index('RadID')].split(','))))
                radfindings = list(map(int,list(n[header.index('RadFindingID')].split(','))))
                finding = int(n[header.index('FindingID')])


                if agr_lvl < min_agreement:
                    print(lnd,finding,rads,radfindings, "has agreement level < "+min_agreement)
                    continue
                else:
                    print(lnd,finding,rads,radfindings)

                # Read scan
                if lnd!=lndloaded:
                        [scan,spacing,origin,transfmat] =  readMhd(os.path.join(self.root,'data0/LNDb-{:04}.mhd'.format(lnd))) 
                        df.loc[rownum,'total_scans'] = scan.shape[0]
                        transfmat_toimg,transfmat_toworld = getImgWorldTransfMats(spacing,transfmat)
                        lndloaded = lnd

                # Convert coordinates to image
                ctr = convertToImgCoord(ctr,origin,transfmat_toimg)                
                coords = []
                for rad,radfinding in zip(rads,radfindings):
                        # Read segmentation mask

                        start=-1
                        end=-1
                        if is_nol == 1: 
                            [mask,_,_,_] =  readMhd(os.path.join(self.root,'masks/LNDb-{:04}_rad{}.mhd'.format(lnd,rad)))

                            # Extract cube around nodule
                            scan_cube = extractCube(scan,spacing,ctr)
                            masknod = copy.copy(mask)
                            masknod[masknod!=radfinding] = 0
                            masknod[masknod>0] = 1
                            start,end= find_first(masknod), find_last(masknod)

                            if nodEqDiam(vol)<=3 :
                                xyz=ctr
                                xyz = np.array([xyz[i] for i in [2,1,0]],np.int)
                                spacing = np.array([spacing[i] for i in [2,1,0]])
                                scan_halfcube_size = np.array(nodEqDiam(vol)/spacing/2,np.int)
                                nodule_center=xyz[0]
                                start, end = max(nodule_center-2,0),min(nodule_center+2,masknod.shape[0])
                        coords.append([start,end])
                min_coords=min([x[0] for x in coords])
                max_coords=max([x[1] for x in coords])
                df.loc[rownum, 'coords'] = str([min_coords,max_coords])
        df.to_csv(os.path.join(self.root, LNDB_SERIES_CSV))
        return df
    @staticmethod
    def spec():
        return [
            Input3dSpec(
                input_size=LNDB_INPUT_SIZE, patch_size=LNDB_PATCH_SIZE, in_channels=1
            ),
        ]
