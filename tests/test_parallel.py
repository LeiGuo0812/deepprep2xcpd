"""Exercise real spawn workers, scientific equivalence and publication safety."""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import nibabel as nb
import numpy as np

import test_inplace as fixtures
from deepprep_to_xcpd import convert, reader_validate, write_json
from inplace import rollback, sha


class ParallelConversionTests(unittest.TestCase):
    def setUp(self):
        fixtures.ConversionTests.setUp(self)
        second=self.root/'sub-002'
        shutil.copytree(self.root/'sub-001',second)
        for path in list(second.rglob('*')):
            if path.is_file():
                path.rename(path.with_name(path.name.replace('sub-001','sub-002')))
        manifest=json.loads(self.manifest.read_text())
        record=json.loads(json.dumps(manifest['subjects'][0]).replace('sub-001','sub-002'))
        # Reverse labels deliberately: report order must follow the manifest.
        manifest['subjects']=[record,*manifest['subjects']]
        write_json(self.manifest,manifest)

    def run_convert(self,output,jobs=2,layout='separate',mode='copy'):
        convert(argparse.Namespace(manifest=self.manifest,output=output,
                                   jobs=jobs,layout=layout,mode=mode))

    def validation(self,root):
        return json.loads((root/'code/adapter/validation.json').read_text())

    def snapshot(self):
        return {str(p.relative_to(self.root)):sha(p) for p in self.root.rglob('*') if p.is_file()}

    def assert_originals(self,before):
        for rel,value in before.items():
            self.assertEqual(sha(self.root/rel),value,rel)

    def test_parallel_copy_matches_serial_scientific_outputs_and_manifest_order(self):
        serial=self.base/'serial';parallel=self.base/'parallel'
        before=self.snapshot()
        self.run_convert(serial,jobs=1)
        # Exercise spawn from the actual CLI entry point as used in Docker.
        command=[sys.executable,str(Path(__file__).resolve().parents[1]/'deepprep_to_xcpd.py'),
                 'convert','--manifest',str(self.manifest),'--output',str(parallel),
                 '--layout','separate','--mode','copy','--jobs','2']
        process=subprocess.run(command,capture_output=True,text=True,timeout=90)
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        left=self.validation(serial);right=self.validation(parallel)
        self.assertEqual([r['subject'] for r in right['subjects']],['sub-002','sub-001'])
        self.assertEqual(right['execution']['effective_jobs'],2)
        self.assertEqual(right['execution']['start_method'],'spawn')
        # Both workers actually executed scientific work, outside the parent.
        pids={r['worker_pid'] for r in right['subjects']}
        self.assertEqual(len(pids),2)
        self.assertNotIn(os.getpid(),pids)
        for a,b in zip(left['subjects'],right['subjects']):
            for key in ('seconds','worker_pid'):
                a.pop(key);b.pop(key)
            self.assertEqual(a,b)
        for p in serial.rglob('*.nii.gz'):
            other=parallel/p.relative_to(serial)
            np.testing.assert_array_equal(nb.load(p).get_fdata(),nb.load(other).get_fdata())
            np.testing.assert_array_equal(nb.load(p).affine,nb.load(other).affine)
        self.assertEqual(reader_validate(parallel),right['reader'])
        self.assert_originals(before)

    def test_parallel_inplace_commits_once_and_rolls_back_both_subjects(self):
        before=self.snapshot()
        self.run_convert(self.root,layout='inplace')
        journals=list((self.root/'code/adapter/inplace-runs').glob('*/journal.json'))
        self.assertEqual(len(journals),1)
        self.assertEqual(json.loads(journals[0].read_text())['state'],'COMMITTED')
        self.assertEqual(len(reader_validate(self.root)),2)
        self.assertEqual(self.validation(self.root)['execution']['effective_jobs'],2)
        rollback(self.root,journals[0].parent.name)
        self.assert_originals(before)
        self.assertFalse(list(self.root.glob('sub-*/anat/*_mode-image_xfm.nii.gz')))

    def test_failed_worker_prevents_publication_for_both_layouts(self):
        manifest=json.loads(self.manifest.read_text())
        confounds=Path(manifest['subjects'][0]['runs'][0]['confounds'])
        lines=confounds.read_text().splitlines()
        confounds.write_text('\n'.join(lines[:-1])+'\n')
        before=self.snapshot()
        children={p.pid for p in multiprocessing.active_children()}
        for layout in ('separate','inplace'):
            with self.subTest(layout=layout):
                output=self.base/'failed' if layout=='separate' else self.root
                with patch('deepprep_to_xcpd.reader_validate') as reader:
                    with self.assertRaisesRegex(RuntimeError,'sub-002.*Confounds rows'):
                        self.run_convert(output,layout=layout)
                    reader.assert_not_called()
                self.assert_originals(before)
                self.assertFalse(list((self.root/'code/adapter/inplace-runs').glob('*/journal.json')))
                if layout=='separate':self.assertFalse(output.exists())
                failed=list(self.base.glob(f'.{output.name}.building-*/code/adapter/FAILED.json'))
                self.assertEqual(len(failed),1)
                self.assertEqual(json.loads(failed[0].read_text())['phase'],'STAGING_SUBJECTS')
                self.assertEqual({p.pid for p in multiprocessing.active_children()},children)

    def test_single_subject_caps_requested_workers_and_symlink_mode_works(self):
        manifest=json.loads(self.manifest.read_text())
        manifest['subjects']=manifest['subjects'][:1]
        write_json(self.manifest,manifest)
        output=self.base/'links'
        self.run_convert(output,jobs=4,mode='symlink')
        result=self.validation(output)
        self.assertEqual(result['execution']['requested_jobs'],4)
        self.assertEqual(result['execution']['effective_jobs'],1)
        self.assertEqual(result['execution']['start_method'],'serial')
        self.assertTrue(next(output.glob('sub-*/ses-*/func/*_bold.nii.gz')).is_symlink())
        self.assertEqual(len(reader_validate(output)),1)

    def test_parallel_symlink_preserves_originals_and_links_remain_readable(self):
        before=self.snapshot()
        output=self.base/'parallel-links'
        self.run_convert(output,mode='symlink')
        bolds=list(output.glob('sub-*/ses-*/func/*_bold.nii.gz'))
        self.assertEqual(len(bolds),2)
        self.assertTrue(all(p.is_symlink() and p.exists() for p in bolds))
        self.assertEqual(len(reader_validate(output)),2)
        self.assert_originals(before)

    def test_interruption_retains_diagnostics_without_publishing(self):
        before=self.snapshot()
        with patch('deepprep_to_xcpd._stage_records',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_convert(self.root,layout='inplace')
        self.assert_originals(before)
        self.assertFalse(list((self.root/'code/adapter/inplace-runs').glob('*/journal.json')))
        failed=next(self.base.glob('.BOLD.building-*/code/adapter/FAILED.json'))
        self.assertEqual(json.loads(failed.read_text())['error_type'],'KeyboardInterrupt')

    def test_invalid_jobs_rejected_before_lock_or_staging(self):
        for jobs in (0,-1,17,True,1.5):
            with self.subTest(jobs=jobs),self.assertRaisesRegex(ValueError,'--jobs'):
                self.run_convert(self.root,jobs=jobs,layout='inplace')
        self.assertFalse((self.root/'code').exists())
        self.assertFalse(list(self.base.glob('.*.building-*')))


if __name__=='__main__':
    unittest.main()
