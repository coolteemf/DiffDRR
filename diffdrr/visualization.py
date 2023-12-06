# AUTOGENERATED! DO NOT EDIT! File to edit: ../notebooks/api/04_visualization.ipynb.

# %% ../notebooks/api/04_visualization.ipynb 3
from __future__ import annotations

import tempfile

import imageio.v3 as iio
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from tqdm import tqdm

# %% auto 0
__all__ = ['plot_drr', 'animate', 'drr_to_mesh', 'img_to_mesh']

# %% ../notebooks/api/04_visualization.ipynb 5
import torch


def plot_drr(
    img: torch.Tensor,
    title: str | None = None,
    ticks: bool | None = True,
    axs: matplotlib.axes._axes.Axes | None = None,
    cmap: str = "gray",
    **imshow_kwargs,
):
    """Plot an image generated by a DRR module."""

    if axs is None:
        fig, axs = plt.subplots(ncols=len(img), figsize=(10, 5))
    if len(img) == 1:
        axs = [axs]
    for img, ax in zip(img, axs):
        ax.imshow(img.squeeze().cpu().detach(), cmap=cmap, **imshow_kwargs)
        _, height, width = img.shape
        ax.xaxis.tick_top()
        ax.set(
            xlabel=title,
            xticks=[0, width - 1],
            xticklabels=[1, width],
            yticks=[0, height - 1],
            yticklabels=[1, height],
        )
        if ticks is False:
            ax.set_xticks([])
            ax.set_yticks([])
    return axs

# %% ../notebooks/api/04_visualization.ipynb 6
import pathlib

import pandas

from .drr import DRR


def animate(
    out: str | pathlib.Path,  # Savepath
    df: pandas.DataFrame,
    drr: DRR,
    parameterization: str,
    convention: str = None,
    ground_truth: torch.Tensor | None = None,
    verbose: bool = True,
    device="cpu",
    **kwargs,  # To pass to imageio.v3.imwrite
):
    """Animate the optimization of a DRR."""
    # Make the axes
    if ground_truth is None:

        def make_fig():
            fig, ax_opt = plt.subplots(
                figsize=(3, 3),
                constrained_layout=True,
            )
            return fig, ax_opt

    else:

        def make_fig(ground_truth):
            fig, (ax_fix, ax_opt) = plt.subplots(
                ncols=2,
                figsize=(6, 3),
                constrained_layout=True,
            )
            plot_drr(ground_truth, axs=ax_fix)
            ax_fix.set(xlabel="Fixed DRR")
            return fig, ax_opt

    # Compute DRRs, plot, and save to temporary folder
    if verbose:
        itr = tqdm(df.iterrows(), desc="Precomputing DRRs", total=len(df), ncols=75)
    else:
        itr = df.iterrows()

    with tempfile.TemporaryDirectory() as tmpdir:
        idxs = []
        for idx, row in itr:
            fig, ax_opt = make_fig() if ground_truth is None else make_fig(ground_truth)
            params = row[["alpha", "beta", "gamma", "bx", "by", "bz"]].values
            rotations = (
                torch.tensor(row[["alpha", "beta", "gamma"]].values)
                .unsqueeze(0)
                .to(device)
            )
            translations = (
                torch.tensor(row[["bx", "by", "bz"]].values).unsqueeze(0).to(device)
            )
            itr = drr(rotations, translations, parameterization, convention)
            _ = plot_drr(itr, axs=ax_opt)
            ax_opt.set(xlabel=f"Moving DRR (loss = {row['loss']:.3f})")
            fig.savefig(f"{tmpdir}/{idx}.png")
            plt.close(fig)
            idxs.append(idx)
        frames = np.stack(
            [iio.imread(f"{tmpdir}/{idx}.png") for idx in idxs],
            axis=0,
        )

    # Make the animation
    return iio.imwrite(out, frames, **kwargs)

# %% ../notebooks/api/04_visualization.ipynb 9
import pyvista
import vtk

from .drr import DRR

vtk.vtkLogger.SetStderrVerbosity(vtk.vtkLogger.ConvertToVerbosity(-1))

# %% ../notebooks/api/04_visualization.ipynb 10
def drr_to_mesh(
    drr: DRR,
    method: str,  # Either `surface_nets` or `marching_cubes`
    threshold: float = 300,  # Min value for marching cubes (Hounsfield units)
    verbose: bool = True,  # Display progress bars for mesh processing steps
):
    """
    Convert the CT in a DRR object into a mesh.

    If using `method=="surface_nets"`, ensure you have `pyvista>=0.43` and `vtk>=9.3` installed.

    The mesh processing steps are:

    1. Keep only largest connected components
    2. Smooth
    3. Decimate (if `method=="marching_cubes"`)
    4. Fill any holes
    5. Clean (remove any redundant vertices/edges)
    """
    # Turn the CT into a PyVista object and run marching cubes
    grid = pyvista.ImageData(
        dimensions=drr.volume.shape,
        spacing=drr.spacing,
        origin=(0, 0, 0),
    )

    if method == "marching_cubes":
        mesh = grid.contour(
            isosurfaces=1,
            scalars=drr.volume.cpu().numpy().flatten(order="F"),
            rng=[threshold, torch.inf],
            method="marching_cubes",
            progress_bar=verbose,
        )
    elif method == "surface_nets":
        grid.point_data["values"] = (
            drr.volume.cpu().numpy().flatten(order="F") > threshold
        )
        try:
            mesh = grid.contour_labeled(smoothing=True, progress_bar=verbose)
        except AttributeError as e:
            raise AttributeError(
                f"{e}, ensure you are using pyvista>=0.43 and vtk>=9.3"
            )
    else:
        raise ValueError(
            f"method must be `marching_cubes` or `surface_nets`, not {method}"
        )

    # Process the mesh
    mesh.extract_largest(inplace=True, progress_bar=verbose)
    mesh.point_data.clear()
    mesh.cell_data.clear()
    mesh.smooth_taubin(
        n_iter=100,
        feature_angle=120.0,
        boundary_smoothing=False,
        feature_smoothing=False,
        non_manifold_smoothing=True,
        normalize_coordinates=True,
        inplace=True,
        progress_bar=verbose,
    )
    if method == "marching_cubes":
        mesh.decimate_pro(0.25, inplace=True, progress_bar=verbose)
    mesh.fill_holes(100, inplace=True, progress_bar=verbose)
    mesh.clean(inplace=True, progress_bar=verbose)
    return mesh

# %% ../notebooks/api/04_visualization.ipynb 11
def img_to_mesh(
    drr: DRR, rotations, translations, parameterization, convention=None, **kwargs
):
    """
    For a given pose (not batched), turn the camera and detector into a mesh.
    Additionally, render the DRR for the pose. Convert into a texture that
    can be applied to the detector mesh.
    """
    # Turn DRR img into a texture that can be applied to a mesh
    img = drr(rotations, translations, parameterization, convention)
    img = img.detach().cpu().squeeze().numpy()
    img = (img - img.min()) / (img.max() - img.min())
    img = (255.0 * img).astype(np.uint8)
    texture = pyvista.numpy_to_texture(img)

    # Make a mesh for the camera and the principal ray
    source, target = drr.detector(rotations, translations, parameterization, convention)
    source = source.squeeze().cpu().numpy()
    target = target.reshape(drr.detector.height, drr.detector.width, 3).cpu().numpy()
    principal_ray = pyvista.Line(source, target.mean(axis=0).mean(axis=0))
    camera = _make_camera_frustum_mesh(source, target, size=0.125)

    # Make a mesh for the detector plane
    detector = pyvista.StructuredGrid(
        target[..., 0],
        target[..., 1],
        target[..., 2],
    )
    detector.add_field_data([drr.detector.height], "height")
    detector.add_field_data([drr.detector.width], "width")
    detector.texture_map_to_plane(
        origin=target[-1, 0],
        point_u=target[-1, -1],
        point_v=target[0, 0],
        inplace=True,
    )

    return camera, detector, texture, principal_ray

# %% ../notebooks/api/04_visualization.ipynb 12
import numpy as np


def _make_camera_frustum_mesh(source, target, size=0.125):
    vertices = np.stack(
        [
            source + size * (target[0, 0] - source),
            source + size * (target[-1, 0] - source),
            source + size * (target[-1, -1] - source),
            source + size * (target[0, -1] - source),
            source,
        ]
    )
    faces = np.hstack(
        [
            [4, 0, 1, 2, 3],
            [3, 0, 1, 4],
            [3, 1, 2, 4],
            [3, 0, 3, 4],
            [3, 2, 3, 4],
        ]
    )
    return pyvista.PolyData(vertices, faces)
