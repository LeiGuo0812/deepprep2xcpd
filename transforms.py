"""Physical-coordinate transform routines for SynthMorph RAS-mm displacements.

Forward image resampling pulls T1w coordinates from the template lattice.
NIfTI geometry remains RAS; ITK displacement vector components are LPS mm.
Numerical inversion is validated by the calling adapter, not assumed exact.
"""
import subprocess
import nibabel as nb
import numpy as np
from scipy.ndimage import map_coordinates

def affine_points(affine, xyz):
    return xyz @ affine[:3, :3].T + affine[:3, 3]


def sample(field, inverse_affine, xyz):
    coords = affine_points(inverse_affine, xyz).T
    return np.column_stack([map_coordinates(field[..., i], coords, order=1,
                                           mode='nearest', prefilter=False)
                            for i in range(3)])


def field_image(ras_field, affine, path):
    lps = np.asarray(ras_field, dtype=np.float32).copy()
    lps[..., :2] *= -1
    im = nb.Nifti1Image(lps[..., None, :], affine)
    im.header.set_intent('vector')
    im.header.set_xyzt_units('mm')
    nb.save(im, str(path))


def apply(src, ref, dest, transform, interp='Linear'):
    result = subprocess.run(['antsApplyTransforms', '-d', '3', '-i', str(src),
                             '-r', str(ref), '-o', str(dest), '-t', str(transform),
                             '-n', interp], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)


def inverse_field(field, affine, reference, brain, native_brain):
    """Invert pull-back map F(x)=x+d(x) on the supplied native reference lattice.

    Affine-preconditioned damped iteration; nearest boundary extension is used
    to populate the rectangular field outside its scientifically valid domain.
    Coverage and residuals are separately checked within the native brain.
    """
    inv_affine = np.linalg.inv(affine)
    points = np.argwhere(brain)[::20]
    x = affine_points(affine, points)
    y = x + field[tuple(points.T)]
    fit = np.linalg.lstsq(np.column_stack([x, np.ones(len(x))]), y, rcond=None)[0]
    matrix, offset = fit[:3].T, fit[3]
    preconditioner = np.linalg.inv(matrix)
    shape = reference.shape[:3]
    output = np.empty(shape + (3,), dtype=np.float32)
    iteration_counts = []
    for start in range(0, shape[0], 8):
        stop = min(start + 8, shape[0])
        grid = np.indices((stop-start, shape[1], shape[2]), dtype=np.float64)
        grid[0] += start
        target = affine_points(reference.affine, grid.reshape(3, -1).T)
        estimate = (target-offset) @ preconditioner.T
        for iteration in range(100):
            residual = estimate + sample(field, inv_affine, estimate) - target
            if np.max(np.abs(residual)) < 0.0005:
                break
            correction = residual @ preconditioner.T
            estimate -= 0.8 * correction
        # Strong local gradients can make the fast iteration oscillate at a
        # handful of points. Refine those brain points with a smaller step.
        residual = estimate + sample(field, inv_affine, estimate) - target
        refine = native_brain[start:stop].reshape(-1) & (np.linalg.norm(residual, axis=1) > 0.0005)
        if np.any(refine):
            x, y = estimate[refine].copy(), target[refine]
            for _ in range(500):
                residual = x + sample(field, inv_affine, x) - y
                if np.max(np.abs(residual)) < 0.0002:
                    break
                x -= 0.2 * residual
            estimate[refine] = x
        if not np.isfinite(estimate).all():
            raise ValueError('Non-finite inverse field')
        output[start:stop] = (estimate-target).reshape(stop-start, shape[1], shape[2], 3)
        iteration_counts.append(iteration+1)
    return output, iteration_counts


def error_summary(values):
    return {'median_mm': float(np.median(values)),
            'p95_mm': float(np.percentile(values, 95)),
            'p99_mm': float(np.percentile(values, 99)),
            'max_mm': float(np.max(values)),
            'fraction_over_1mm': float(np.mean(values > 1))}
