# AUTOGENERATED! DO NOT EDIT! File to edit: ../notebooks/api/03_data.ipynb.

# %% ../notebooks/api/03_data.ipynb 3
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torchio import LabelMap, ScalarImage, Subject
from torchio.transforms.preprocessing import ToCanonical
import pandas as pd

# %% auto 0
__all__ = ['load_example_ct', 'read']

# %% ../notebooks/api/03_data.ipynb 4
def load_example_ct(labels=None) -> Subject:
    """Load an example chest CT for demonstration purposes."""
    datadir = Path(__file__).resolve().parent / "data"
    filename = datadir / "cxr.nii.gz"
    labelmap = datadir / "mask.nii.gz"
    structures = pd.read_csv(datadir / "structures.csv")
    return read(filename, labelmap, labels, structures=structures)

# %% ../notebooks/api/03_data.ipynb 5
def read(
    filename: str | Path,  # Path to CT volume
    labelmap: str | Path = None,  # Path to a labelmap for the CT volume
    labels: int | list = None,  # Labels from the mask of structures to render
    **kwargs,  # Any additional information to be stored in the torchio.Subject
) -> Subject:
    """
    Read an image volume from a variety of formats, and optionally, any
    given labelmap for the volume. Converts volume to a RAS+ coordinate
    system and moves the volume isocenter to the world origin.
    """
    # Read the volume from a filename
    volume = ScalarImage(filename)
    density = transform_hu_to_density(volume.data)

    # If a labelmap is passed, read the mask
    if labelmap is not None:
        mask = LabelMap(labelmap)
    else:
        mask = None

    # Package the subject
    subject = Subject(
        volume=volume,
        mask=mask,
        density=density,
        **kwargs,
    )

    # Canonicalize the images by converting to RAS+ and moving the
    # Subject's isocenter to the origin in world coordinates
    subject = canonicalize(subject)

    # Apply mask
    if labels is not None:
        if isinstance(labels, int):
            labels = [labels]
        mask = torch.any(
            torch.stack([mask.data.squeeze() == idx for idx in labels]), dim=0
        )
        subject.density = subject.density * mask

    return subject

# %% ../notebooks/api/03_data.ipynb 6
def canonicalize(subject):
    # Convert to RAS+ coordinate system
    subject = ToCanonical()(subject)

    # Move the Subject's isocenter to the origin in world coordinates
    for image in subject.get_images(intensity_only=False):
        isocenter = image.get_center()
        Tinv = np.array(
            [
                [1.0, 0.0, 0.0, -isocenter[0]],
                [0.0, 1.0, 0.0, -isocenter[1]],
                [0.0, 0.0, 1.0, -isocenter[2]],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
        image.affine = Tinv.dot(image.affine)

    return subject

# %% ../notebooks/api/03_data.ipynb 7
def transform_hu_to_density(volume):
    volume = volume.to(torch.float32)

    air = torch.where(volume <= -800)
    soft_tissue = torch.where((-800 < volume) & (volume <= 350))
    bone = torch.where(350 < volume)

    density = torch.empty_like(volume)
    density[air] = volume[soft_tissue].min()
    density[soft_tissue] = volume[soft_tissue]
    density[bone] = volume[bone]
    density -= density.min()
    density /= density.max()

    return density.squeeze()
