"""Exercise version-neutral manifests and reader calls through the public path."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deepprep_to_xcpd import PROFILE, _convert, reader_validate, write_json
import test_adapter as fixtures


class VersionPolicyTests(unittest.TestCase):
    def test_legacy_and_unversioned_manifests_reach_data_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp)/'manifest.json'
            for profile in [PROFILE, 'deepprep-24.1.2-synthmorph-ras-mm']:
                write_json(manifest, dict(schema_version=1, profile=profile,
                           space='MNI152NLin6Asym', task='rest', subjects=[]))
                with self.assertRaisesRegex(ValueError, 'Empty subject selection'):
                    _convert(argparse.Namespace(manifest=manifest))
            write_json(manifest, dict(schema_version=1, profile='absolute-voxel-coordinates'))
            with self.assertRaisesRegex(ValueError, 'displacement convention'):
                _convert(argparse.Namespace(manifest=manifest))

    def test_session_selection_with_both_reader_api_shapes(self):
        # Real PyBIDS indexing and existing mixed-session fixture. Test doubles
        # exercise API dispatch only; this is not an installed-XCP-D validation.
        def selection(layout, input_type, participant_label, bids_filters, file_format):
            anatomy = dict(subject=participant_label, datatype='anat', return_type='file')
            return dict(
                bold=layout.get(subject=participant_label, return_type='file', datatype='func',
                                desc='preproc', suffix='bold', extension='.nii.gz', **bids_filters['bold']),
                anat_to_template_xfm=layout.get(**anatomy, suffix='xfm', to='MNI152NLin6Asym', extension='.nii.gz')[0],
                template_to_anat_xfm=layout.get(**anatomy, suffix='xfm', to='T1w', extension='.nii.gz')[0])

        seen = []
        def modern(layout, input_type, participant_label, bids_filters, file_format,
                   anat_session, func_sessions):
            seen.extend(func_sessions)
            return selection(layout, input_type, participant_label, bids_filters, file_format)

        def run_reader(layout, bold_file, file_format, target_space):
            self.assertTrue(Path(bold_file).is_file())
            return {'bold': bold_file}

        for function in [selection, modern]:
            with patch('deepprep_to_xcpd.load_readers', return_value=(function, run_reader)), \
                 patch('importlib.metadata.version', side_effect=AssertionError('version gate')):
                fixtures.SelectionTests('test_reader_includes_sessionless_and_session_runs').test_reader_includes_sessionless_and_session_runs()
        self.assertIn('B', seen)
        self.assertEqual(len(seen), 2)


if __name__ == '__main__':
    unittest.main()
