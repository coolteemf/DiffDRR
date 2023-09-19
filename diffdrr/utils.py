# AUTOGENERATED! DO NOT EDIT! File to edit: ../notebooks/api/06_utils.ipynb.

# %% auto 0
__all__ = ['install_pytorch3d']

# %% ../notebooks/api/06_utils.ipynb 5
import subprocess
import sys

import torch


def install_pytorch3d():
    subprocess.run(["pip", "install", "fvcore", "iopath"])
    subprocess.run(
        [
            "pip",
            "install",
            "--no-index",
            "--no-cache-dir",
            "pytorch3d",
            "-f",
            "pytorch3d @ git+https://github.com/facebookresearch/pytorch3d.git@stable",
        ]
    )
