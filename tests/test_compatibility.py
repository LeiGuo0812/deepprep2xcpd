"""Reader API and provenance regressions, runnable with the standard library."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compatibility import load_readers, reader_kwargs, source_generated_by


class CompatibilityTests(unittest.TestCase):
    def test_reader_with_and_without_session_arguments(self):
        def old(layout, input_type, participant_label, bids_filters, file_format):
            return participant_label, bids_filters

        def current(layout, input_type, participant_label, bids_filters, file_format,
                    anat_session, func_sessions):
            return func_sessions

        required = dict(layout=object(), input_type='fmriprep', participant_label='001',
                        bids_filters={'bold': {'task': 'rest'}}, file_format='nifti')
        optional = dict(anat_session=None, func_sessions=['A', 'no-session'])
        self.assertEqual(old(**reader_kwargs(old, required, optional)),
                         ('001', {'bold': {'task': 'rest'}}))
        self.assertEqual(current(**reader_kwargs(current, required, optional)),
                         ['A', 'no-session'])

    def test_additional_optional_arguments_keep_reader_defaults(self):
        def future(layout, input_type, participant_label, bids_filters, file_format,
                   new_setting='default'):
            return new_setting
        kwargs = dict(layout=None, input_type='fmriprep', participant_label='001',
                      bids_filters={}, file_format='nifti')
        self.assertEqual(future(**reader_kwargs(future, kwargs)), 'default')

    def test_unknown_required_arguments_report_actual_api(self):
        def changed(layout, new_required):
            pass
        with self.assertRaisesRegex(ValueError, 'Incompatible XCP-D reader API.*new_required'):
            reader_kwargs(changed, {'layout': None})

    def test_reader_internal_typeerror_is_not_retried(self):
        calls = []
        def broken(layout):
            calls.append(layout)
            raise TypeError('reader computation failed')
        with self.assertRaisesRegex(TypeError, 'reader computation failed'):
            broken(**reader_kwargs(broken, {'layout': 'input'}))
        self.assertEqual(calls, ['input'])

    def test_preflight_does_not_consult_version_metadata(self):
        def collect_data(layout, input_type, participant_label, bids_filters, file_format):
            pass
        def collect_run_data(layout, bold_file, file_format, target_space):
            pass
        module = types.SimpleNamespace(collect_data=collect_data, collect_run_data=collect_run_data)
        with patch('compatibility.importlib.import_module', return_value=module), \
             patch('importlib.metadata.version', side_effect=AssertionError('version gate')):
            self.assertEqual(load_readers(), (collect_data, collect_run_data))

    def test_missing_dependency_and_missing_function_are_actionable(self):
        with patch('compatibility.importlib.import_module', side_effect=ImportError('dependency missing')):
            with self.assertRaisesRegex(ValueError, 'dependency missing'):
                load_readers()
        with patch('compatibility.importlib.import_module', return_value=types.SimpleNamespace()):
            with self.assertRaisesRegex(ValueError, 'missing callable collect_data'):
                load_readers()

    def test_source_versions_and_other_generators_are_preserved(self):
        entries = [{'Name': 'DeepPrep', 'Version': '30.4-custom', 'CodeURL': 'https://example.org'},
                   {'Name': 'OtherPreprocessing', 'Version': '3'}]
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'dataset_description.json').write_text(json.dumps({'GeneratedBy': entries}))
            self.assertEqual(source_generated_by({'source_dataset': tmp}), entries)

    def test_missing_version_is_not_invented_from_legacy_profile(self):
        self.assertEqual(source_generated_by({'profile': 'deepprep-24.1.2-synthmorph-ras-mm'}),
                         [{'Name': 'DeepPrep'}])
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'dataset_description.json').write_text('{"GeneratedBy":[{"Name":"DeepPrep"}]}')
            self.assertEqual(source_generated_by({'source_dataset': tmp}), [{'Name': 'DeepPrep'}])


if __name__ == '__main__':
    unittest.main()
