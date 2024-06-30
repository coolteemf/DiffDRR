# AUTOGENERATED! DO NOT EDIT! File to edit: ../notebooks/api/01_renderers.ipynb.

# %% auto 0
__all__ = ['Siddon', 'Trilinear']

# %% ../notebooks/api/01_renderers.ipynb 3
import torch
from torch.nn.functional import grid_sample

# %% ../notebooks/api/01_renderers.ipynb 7
class Siddon(torch.nn.Module):
    """Differentiable X-ray renderer implemented with Siddon's method for exact raytracing."""

    def __init__(
        self,
        mode: str = "nearest",  # Interpolation mode for grid_sample
        stop_gradients_through_grid_sample: bool = False,  # Apply torch.no_grad when calling grid_sample
        filter_intersections_outside_volume: bool = True,  # Use alphamin/max to filter the intersections
        eps: float = 1e-8,  # Small constant to avoid div by zero errors
    ):
        super().__init__()
        self.mode = mode
        self.stop_gradients_through_grid_sample = stop_gradients_through_grid_sample
        self.filter_intersections_outside_volume = filter_intersections_outside_volume
        self.eps = eps

    def dims(self, volume):
        return torch.tensor(volume.shape).to(volume)

    def forward(
        self,
        volume,
        source,
        target,
        align_corners=True,
        mask=None,
    ):
        dims = self.dims(volume)

        # Calculate the intersections of each ray with the planes comprising the CT volume
        alphas = _get_alphas(
            source,
            target,
            dims,
            self.eps,
            self.filter_intersections_outside_volume,
        )

        # Calculate the midpoint of every pair of adjacent intersections
        # These midpoints lie exclusively in a single voxel
        alphamid = (alphas[..., 0:-1] + alphas[..., 1:]) / 2

        # Get the XYZ coordinate of each midpoint (normalized to [-1, +1]^3)
        xyzs = _get_xyzs(alphamid, source, target, dims, self.eps)

        # Use torch.nn.functional.grid_sample to lookup the values of each intersected voxel
        if self.stop_gradients_through_grid_sample:
            with torch.no_grad():
                img = _get_voxel(volume, xyzs, self.mode, align_corners=align_corners)
        else:
            img = _get_voxel(volume, xyzs, self.mode, align_corners=align_corners)

        # Weight each intersected voxel by the length of the ray's intersection with the voxel
        intersection_length = torch.diff(alphas, dim=-1)
        img = img * intersection_length

        # Handle optional masking
        if mask is None:
            img = img.sum(dim=-1)
            img = img.unsqueeze(1)
        else:
            # Thanks to @Ivan for the clutch assist w/ pytorch tensor ops
            # https://stackoverflow.com/questions/78323859/broadcast-pytorch-array-across-channels-based-on-another-array/78324614#78324614
            B, D, _ = img.shape
            C = int(mask.max().item() + 1)
            channels = _get_voxel(mask, xyzs, align_corners=align_corners).long()
            img = (
                torch.zeros(B, C, D)
                .to(img)
                .scatter_add_(1, channels.transpose(-1, -2), img.transpose(-1, -2))
            )

        # Multiply by ray length such that the proportion of attenuated energy is unitless
        raylength = (target - source + self.eps).norm(dim=-1)
        img *= raylength.unsqueeze(1)
        return img

# %% ../notebooks/api/01_renderers.ipynb 8
def _get_alphas(source, target, dims, eps, filter_intersections_outside_volume):
    """Calculates the parametric intersections of each ray with the planes of the CT volume."""
    # Parameterize the parallel XYZ planes that comprise the CT volumes
    alphax = torch.arange(dims[0] + 1).to(source) - 0.5
    alphay = torch.arange(dims[1] + 1).to(source) - 0.5
    alphaz = torch.arange(dims[2] + 1).to(source) - 0.5

    # Calculate the parametric intersection of each ray with every plane
    sx, sy, sz = source[..., 0:1], source[..., 1:2], source[..., 2:3]
    tx, ty, tz = target[..., 0:1], target[..., 1:2], target[..., 2:3]
    alphax = (alphax.expand(len(source), 1, -1) - sx) / (tx - sx + eps)
    alphay = (alphay.expand(len(source), 1, -1) - sy) / (ty - sy + eps)
    alphaz = (alphaz.expand(len(source), 1, -1) - sz) / (tz - sz + eps)
    alphas = torch.cat([alphax, alphay, alphaz], dim=-1)

    # Sort the intersections
    alphas = torch.sort(alphas, dim=-1).values
    if filter_intersections_outside_volume:
        alphas = _filter_intersections_outside_volume(alphas, source, target, dims, eps)
    return alphas


def _filter_intersections_outside_volume(alphas, source, target, dims, eps):
    """Remove interesections that are outside of the volume for all rays."""
    alphamin, alphamax = _get_alpha_minmax(source, target, dims, eps)
    good_idxs = torch.logical_and(alphamin <= alphas, alphas <= alphamax)
    alphas = alphas[..., good_idxs.any(dim=[0, 1])]
    return alphas


def _get_alpha_minmax(source, target, dims, eps):
    """Calculate the first and last intersections of each ray with the volume."""
    sdd = target - source + eps

    alpha0 = (torch.zeros(3).to(source) - source) / sdd
    alpha1 = ((dims - 1).to(source) - source) / sdd
    alphas = torch.stack([alpha0, alpha1])

    alphamin = alphas.min(dim=0).values.max(dim=-1).values.unsqueeze(-1)
    alphamax = alphas.max(dim=0).values.min(dim=-1).values.unsqueeze(-1)

    alphamin = torch.where(alphamin < 0.0, 0.0, alphamin)
    alphamax = torch.where(alphamax > 1.0, 1.0, alphamax)
    return alphamin, alphamax


def _get_xyzs(alpha, source, target, dims, eps):
    """Given a set of rays and parametric coordinates, calculates the XYZ coordinates."""
    # Get the world coordinates of every point parameterized by alpha
    xyzs = (
        source.unsqueeze(-2)
        + alpha.unsqueeze(-1) * (target - source + eps).unsqueeze(2)
    ).unsqueeze(1)

    # Normalize coordinates to be in [-1, +1] for grid_sample
    xyzs = 2 * xyzs / dims - 1
    return xyzs


def _get_voxel(volume, xyzs, mode="nearest", align_corners=True):
    """Wraps torch.nn.functional.grid_sample to sample a volume at XYZ coordinates."""
    batch_size = len(xyzs)
    voxels = grid_sample(
        input=volume.permute(2, 1, 0)[None, None].expand(batch_size, -1, -1, -1, -1),
        grid=xyzs,
        mode=mode,
        align_corners=align_corners,
    )[:, 0, 0]
    return voxels

# %% ../notebooks/api/01_renderers.ipynb 10
class Trilinear(torch.nn.Module):
    """Differentiable X-ray renderer implemented with trilinear interpolation."""

    def __init__(
        self,
        near=0.0,
        far=1.0,
        mode: str = "bilinear",  # Interpolation mode for grid_sample
        filter_intersections_outside_volume: bool = True,  # Use alphamin/max to filter the intersections
        eps: float = 1e-8,  # Small constant to avoid div by zero errors
    ):
        super().__init__()
        self.near = near
        self.far = far
        self.mode = mode
        self.filter_intersections_outside_volume = filter_intersections_outside_volume
        self.eps = eps

    def dims(self, volume):
        return torch.tensor(volume.shape).to(volume)

    def forward(
        self,
        volume,
        source,
        target,
        n_points=500,
        align_corners=True,
        mask=None,
    ):
        dims = self.dims(volume)

        # Sample points along the rays and rescale to [-1, 1]
        alphas = torch.linspace(self.near, self.far, n_points)[None, None].to(volume)
        if self.filter_intersections_outside_volume:
            alphas = _filter_intersections_outside_volume(
                alphas, source, target, dims, self.eps
            )

        # Render the DRR
        # Get the XYZ coordinate of each alpha, normalized for grid_sample
        xyzs = _get_xyzs(alphas, source, target, dims, self.eps)

        # Sample the volume with trilinear interpolation
        img = _get_voxel(volume, xyzs, self.mode, align_corners=align_corners)

        # Handle optional masking
        if mask is None:
            img = img.sum(dim=-1).unsqueeze(1)
        else:
            B, D, _ = img.shape
            C = int(mask.max().item() + 1)
            channels = _get_voxel(mask, xyzs, align_corners=align_corners).long()
            img = (
                torch.zeros(B, C, D)
                .to(img)
                .scatter_add_(1, channels.transpose(-1, -2), img.transpose(-1, -2))
            )

        # Multiply by raylength and return the drr
        raylength = (target - source + self.eps).norm(dim=-1).unsqueeze(1)
        img *= raylength / n_points
        return img
