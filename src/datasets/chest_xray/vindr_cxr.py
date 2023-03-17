#/home/ubuntu/raw/vindr/physionet.org/files/vindr-cxr/1.0.0
import glob
import os
import shutil
from typing import Any

import numpy as np
import pandas as pd
import pydicom
import torch
from PIL import Image
from torchvision import transforms
from torchvision.datasets.vision import VisionDataset
from tqdm import tqdm

from src.datasets.specs import Input2dSpec
from src.utils import count_files

CHEXPERT_LABELS = {
    'No Finding': 0,
    'Enlarged Cardiomediastinum': 1,
    'Cardiomegaly': 2,
    'Lung Opacity': 3,
    'Lung Lesion': 4,
    'Edema': 5,
    'Consolidation': 6,
    'Pneumonia': 7,
    'Atelectasis': 8,
    'Pneumothorax': 9,
    'Pleural Effusion': 10,
    'Pleural Other': 11,
    'Fracture': 12,
    'Support Devices': 13,
}


def any_exist(files):
    return any(map(os.path.exists, files))


class VINDR_CXR(VisionDataset):
    '''A dataset class for the VINDR-CXR dataset (https://physionet.org/content/vindr-cxr/1.0.0/).
    Note that you must register and manually download the data to use this dataset.
    '''
    # Dataset information.

    CHEXPERT_LABELS_IDX = np.array(
        [
            CHEXPERT_LABELS['Atelectasis'], CHEXPERT_LABELS['Enlarged Cardiomediastinum'], CHEXPERT_LABELS['Cardiomegaly'],
            CHEXPERT_LABELS['Lung Opacity'], CHEXPERT_LABELS['Lung Lesion'], CHEXPERT_LABELS['Edema'],
            CHEXPERT_LABELS['Consolidation'], CHEXPERT_LABELS['Pneumonia'], CHEXPERT_LABELS['Atelectasis'],
            CHEXPERT_LABELS['Pneumothorax'], CHEXPERT_LABELS['Pleural Effusion'], CHEXPERT_LABELS['Pleural Other'],
            CHEXPERT_LABELS['Fracture'], CHEXPERT_LABELS['Support Devices']
        ],
        dtype=np.int32
    )
    NUM_CLASSES = 14  # 14 total: len(self.CHEXPERT_LABELS_IDX)
    INPUT_SIZE = (224, 224)
    PATCH_SIZE = (16, 16)
    IN_CHANNELS = 1

    def __init__(self, base_root: str, download: bool = False, train: bool = True) -> None:
        self.root = os.path.join(base_root, 'vindr/physionet.org/files/vindr-cxr/1.0.0')
        super().__init__(self.root)
        self.index_location = self.find_data()
        self.split = 'train' if train else 'test'
        self.build_index()
        self.TRANSFORMS = transforms.Compose(
            [
                transforms.Resize(self.INPUT_SIZE[0] - 1, max_size=self.INPUT_SIZE[0]),
                transforms.ToTensor(),
                transforms.Normalize([0.7635], [0.1404])
            ]
        )

    def find_data(self):
        components = list(map(lambda x: os.path.join(self.root, 'train' + x), ['']))
        # if no data is present, prompt the user to download it
        if not any_exist(components):
            raise FileNotFoundError(
                """
                'Visit https://physionet.org/content/vindr-cxr/1.0.0/ to apply for access'
                'Use: wget -r -N -c -np --user [your user name] --ask-password https://physionet.org/files/vindr-cxr/1.0.0 to download the data'
                'Once you receive the download links, download it in {}'.format(self.root)'
                """
            )

        else:
            return components[0]

    def read_dicom(self, file_path: str, imsize: int):
        """Read pixel array from a DICOM file and apply recale and resize
        operations.
        The rescale operation is defined as the following:
            x = x * RescaleSlope + RescaleIntercept
        The rescale slope and intercept can be found in the DICOM files.
        Args:
            file_path (str): Path to a dicom file.
            resize_shape (int): Height and width for resizing.
        Returns:
            The processed pixel array from a DICOM file.
        """

        # read dicom
        dcm = pydicom.dcmread(file_path)
        pixel_array = dcm.pixel_array

        # rescale
        if 'RescaleIntercept' in dcm:
            intercept = dcm.RescaleIntercept
            slope = dcm.RescaleSlope
            pixel_array = pixel_array * slope + intercept

        return pixel_array

    def dicom_to_jpg(self, fnames, imsize):
        if not os.path.isdir(os.path.join(self.root, self.split + "/" + 'jpegs')):
            os.makedirs(os.path.join(self.root, self.split + "/" + 'jpegs'))
        for i in tqdm(range(len(fnames))):

            dicom_path = fnames[i] + ".dicom"
            img_array = self.read_dicom(dicom_path, imsize)
            img = Image.fromarray(img_array).convert("L")
            img.save(os.path.join(self.root, self.split + "/" + 'jpegs/' + fnames[i].split('/')[-1] + '.jpg'))

    def build_index(self):
        print('Building index...')

        metadata = pd.read_csv(os.path.join(self.root, f"annotations/image_labels_{self.split}.csv"))
        index_file = metadata

        dicom_fnames = np.array(index_file['image_id'].apply(lambda x: os.path.join(self.root, f"{self.split}/{x}")))
        if self.split == "train":
            n_files = 15000
        else:
            n_files = 3000
        if (not os.path.isdir(os.path.join(self.root, self.split + "/" + 'jpegs')
                             )) or count_files(os.path.join(self.root, self.split + "/" + 'jpegs')) < n_files:
            if os.path.isdir(os.path.join(self.root, self.split + "/" + 'jpegs')):
                shutil.rmtree(os.path.join(self.root, self.split + "/" + 'jpegs'))
            self.dicom_to_jpg(fnames=dicom_fnames, imsize=self.INPUT_SIZE[0])
        self.fnames = glob.glob(os.path.join(self.root, self.split + "/" + 'jpegs') + "/*.jpg")
        LABELS_COL = index_file.columns.get_loc("Aortic enlargement")
        end_col = LABELS_COL + len(CHEXPERT_LABELS)
        # missing values occur when no comment is made on a particular diagnosis. we treat this as a negative diagnosis
        self.labels = index_file.iloc[:, range(LABELS_COL, end_col)].values
        self.labels = np.maximum(self.labels, 0)  # convert -1 (unknown) to 0
        print('Done')

    def __len__(self) -> int:
        return len(self.fnames)

    def __getitem__(self, index: int) -> Any:

        fname = self.fnames[index]
        image = Image.open(os.path.join(self.root, fname)).convert("L")
        img = self.TRANSFORMS(image)
        _, h, w = np.array(img).shape
        if h > w:
            dim_gap = img.shape[1] - img.shape[2]
            pad1, pad2 = dim_gap // 2, (dim_gap + (dim_gap % 2)) // 2
            img = transforms.Pad((pad1, 0, pad2, 0))(img)
        elif h == w:
            #edge case 223,223, resize to match 224*224
            dim_gap = self.INPUT_SIZE[0] - h
            pad1, pad2 = dim_gap, dim_gap
            img = transforms.Pad((pad1, pad2, 0, 0))(img)
        else:
            dim_gap = img.shape[2] - img.shape[1]
            pad1, pad2 = dim_gap // 2, (dim_gap + (dim_gap % 2)) // 2
            img = transforms.Pad((0, pad1, 0, pad2))(img)
        label = torch.tensor(self.labels[index][self.CHEXPERT_LABELS_IDX]).long()
        return index, img.float(), label

    @staticmethod
    def num_classes():
        return VINDR_CXR.NUM_CLASSES

    @staticmethod
    def spec():
        return [
            Input2dSpec(input_size=VINDR_CXR.INPUT_SIZE, patch_size=VINDR_CXR.PATCH_SIZE, in_channels=VINDR_CXR.IN_CHANNELS),
        ]
