"""Check reader capabilities and retain source provenance without version gates."""
import importlib
import inspect
import json
from pathlib import Path


def reader_kwargs(function, required, optional=None):
    """Bind known reader arguments; ignore only unsupported optional arguments.

    Inspect the API instead of branching on package version. Binding happens
    before execution so a TypeError inside the reader is never retried/hidden.
    """
    signature = inspect.signature(function)
    kwargs = dict(required)
    for key, value in (optional or {}).items():
        if key in signature.parameters:
            kwargs[key] = value
    try:
        signature.bind(**kwargs)
    except TypeError as exc:
        raise ValueError(
            f'Incompatible XCP-D reader API: {function.__name__}{signature}: {exc}. '
            'Use a reader exposing the required arguments or extend the API adapter; '
            'no particular XCP-D version is required.'
        ) from exc
    return kwargs


def load_readers():
    try:
        module = importlib.import_module('xcp_d.utils.bids')
    except ImportError as exc:
        raise ValueError(
            f'Cannot import the XCP-D reader and its dependencies: {exc}. '
            'Install a complete XCP-D environment or use a suitable container.'
        ) from exc
    functions = []
    for name, required, optional in (
        ('collect_data', dict(layout=None, input_type='fmriprep',
                              participant_label=None, bids_filters={}, file_format='nifti'),
         dict(anat_session=None, func_sessions=[])),
        ('collect_run_data', dict(layout=None, bold_file=None,
                                  file_format='nifti', target_space=None), {}),
    ):
        function = getattr(module, name, None)
        if not callable(function):
            raise ValueError(f'XCP-D reader is missing callable {name}; check the installed API.')
        reader_kwargs(function, required, optional)
        functions.append(function)
    return tuple(functions)


def source_generated_by(manifest):
    """Preserve declared software versions; never infer them from a profile name."""
    dataset = manifest.get('source_dataset')
    if dataset:
        path = Path(dataset) / 'dataset_description.json'
        if path.is_file():
            metadata = json.loads(path.read_text())
            entries = metadata.get('GeneratedBy', [])
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict) or not isinstance(entry.get('Name'), str)
                for entry in entries
            ):
                raise ValueError(f'Invalid GeneratedBy entries: {path}')
            if entries:
                return entries
    # Name is known from the input contract; the release number is not.
    return [{'Name': 'DeepPrep'}]
