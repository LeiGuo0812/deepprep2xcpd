"""Small scientific/selection regressions; no patient data or workflow needed."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import nibabel as nb
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deepprep_to_xcpd import (unique_file, normalized_subject, plan,
                             reader_validate, write_json, validate_run_inputs, REQUIRED_36P)
from transforms import affine_points, sample, field_image, inverse_field, apply


class TransformTests(unittest.TestCase):
    def test_physical_affine_inverse_and_lps_serialization(self):
        # Non-isotropic, flipped grid makes voxel/mm and RAS/LPS mistakes visible.
        shape=(18,20,22)
        affine=np.diag([-2.,3.,2.5,1.]);affine[:3,3]=[20.,-25.,-30.]
        grid=np.indices(shape).reshape(3,-1).T
        x=affine_points(affine,grid)
        matrix=np.diag([1.03,.98,1.02]);shift=np.array([.7,-.8,.3])
        d=(x@matrix.T+shift-x).reshape(shape+(3,)).astype('float32')
        brain=np.zeros(shape,bool);brain[4:-4,4:-4,4:-4]=True
        native=nb.Nifti1Image(np.zeros(shape),affine)
        inv,_=inverse_field(d,affine,native,brain,brain)
        y=x[brain.ravel()]
        exact=(y-shift)@np.linalg.inv(matrix).T
        np.testing.assert_allclose(y+inv[brain],exact,atol=6e-4)
        back=y+inv[brain]
        np.testing.assert_allclose(back+sample(d,np.linalg.inv(affine),back),y,atol=6e-4)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'field.nii.gz';field_image(d,affine,p)
            image=nb.load(p)
            self.assertEqual(image.shape,shape+(1,3))
            self.assertEqual(image.header.get_intent()[0],'vector')
            np.testing.assert_allclose(image.get_fdata()[...,0,:],d*np.array([-1,-1,1]))
            np.testing.assert_allclose(image.affine,affine)

    def test_ants_pull_direction_on_ramp(self):
        # Input intensity equals physical R coordinate. Pull displacement +2 R
        # must therefore increase intensity by exactly 2, not decrease it.
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);shape=(16,17,18)
            aff=np.diag([-2.,2.,3.,1.]);aff[:3,3]=[14,-16,-24]
            x=affine_points(aff,np.indices(shape).reshape(3,-1).T)
            data=x[:,0].reshape(shape).astype('float32')
            nb.save(nb.Nifti1Image(data,aff),p/'src.nii.gz')
            d=np.zeros(shape+(3,),dtype='float32');d[...,0]=2
            field_image(d,aff,p/'xfm.nii.gz')
            apply(p/'src.nii.gz',p/'src.nii.gz',p/'out.nii.gz',p/'xfm.nii.gz')
            out=nb.load(p/'out.nii.gz').get_fdata()
            np.testing.assert_allclose(out[2:-2,2:-2,2:-2],(data+2)[2:-2,2:-2,2:-2],atol=1e-5)


class PlanningPriorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bold = self.root/'BOLD'
        self.work = self.root/'WorkDir'
        self.bold.mkdir()
        self.work.mkdir()
        self.anatomy = ['sub-001_desc-preproc_T1w.nii.gz', 'sub-001_desc-brain_mask.nii.gz',
                        'sub-001_space-T1w_res-2mm_desc-skull_T1w.nii.gz',
                        'sub-001_space-MNI152NLin6Asym_res-02_desc-skull_T1w.nii.gz',
                        'sub-001_from-T1w_to-MNI152NLin6Asym_desc-joint_trans.nii.gz']
        for name in self.anatomy:
            (self.bold/name).write_text('published')
        self.add_run(self.bold, '01')

    def add_run(self, root, run):
        prefix = f'sub-001_task-rest_run-{run}'
        for suffix in ['_space-MNI152NLin6Asym_res-2_desc-preproc_bold.nii.gz',
                       '_space-MNI152NLin6Asym_res-2_desc-preproc_bold.json',
                       '_desc-confounds_timeseries.tsv', '_desc-confounds_timeseries.json',
                       '_space-T1w_desc-brain_mask.nii.gz',
                       '_space-MNI152NLin6Asym_res-2_boldref.nii.gz']:
            (root/(prefix+suffix)).write_text(str(root))

    def make_plan(self, source, number=0, search_roots=None):
        manifest = self.root/f'manifest{number}.json'
        plan(argparse.Namespace(deepprep_dir=source, search_root=search_roots or [], subjects=['001'],
             space='MNI152NLin6Asym', resolution=2, task='rest', manifest=manifest))
        return json.loads(manifest.read_text())

    def test_all_entry_paths_prioritize_complete_bold_without_crawling_work(self):
        self.add_run(self.work, '99')
        # A conflicting work copy must not override a published source.
        for name in self.anatomy:
            (self.work/name).write_text('conflicting work copy')
        import os
        real_walk = os.walk
        walked = []
        def walk(root):
            walked.append(Path(root))
            return real_walk(root)
        with patch('deepprep_to_xcpd.os.walk', side_effect=walk):
            plans = [self.make_plan(p, i) for i, p in enumerate([self.root, self.bold, self.work])]
        self.assertEqual(plans[0], plans[1])
        self.assertEqual(plans[0], plans[2])
        self.assertEqual(walked, [self.bold]*3)
        self.assertEqual(len(plans[0]['subjects'][0]['runs']), 1)

    def test_anatomy_fallback_does_not_add_unpublished_run(self):
        warp = self.anatomy[-1]
        (self.bold/warp).rename(self.work/warp)
        self.add_run(self.work, '99')
        record = self.make_plan(self.bold)['subjects'][0]
        self.assertEqual(record['anatomy']['forward_ras'], str(self.work/warp))
        self.assertEqual([r['prefix'] for r in record['runs']], ['sub-001_task-rest_run-01'])

    def test_optional_reference_is_recovered_before_first_frame_fallback(self):
        name = 'sub-001_task-rest_run-01_space-MNI152NLin6Asym_res-2_boldref.nii.gz'
        (self.bold/name).rename(self.work/name)
        record = self.make_plan(self.bold)['subjects'][0]
        self.assertEqual(record['runs'][0]['boldref'], str(self.work/name))

    def test_missing_bold_tree_recovers_from_work_with_consistent_dataset_binding(self):
        for p in self.bold.iterdir():
            p.rename(self.work/p.name)
        self.bold.rmdir()
        first = self.make_plan(self.root)
        second = self.make_plan(self.work, 1)
        self.assertEqual(first, second)
        self.assertEqual(first['source_dataset'], str(self.bold))
        self.assertEqual(len(first['subjects'][0]['runs']), 1)

    def test_missing_extra_search_root_reports_typo(self):
        with self.assertRaisesRegex(ValueError, 'Missing --search-root'):
            self.make_plan(self.bold, search_roots=[self.root/'typo'])


class SelectionTests(unittest.TestCase):
    def test_confounds_time_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);bold=p/'bold.nii.gz'
            im=nb.Nifti1Image(np.zeros((3,4,5,10)),np.eye(4))
            im.header.set_xyzt_units('mm','sec');im.header.set_zooms((1,1,1,2));nb.save(im,bold)
            write_json(p/'bold.json',{'RepetitionTime':2})
            pd.DataFrame(np.zeros((9,36)),columns=REQUIRED_36P).to_csv(p/'conf.tsv',sep='\t',index=False)
            write_json(p/'conf.json',{})
            run={'prefix':'sub-001_task-rest','resolution':'2','bold':str(bold),
                 'bold_json':str(p/'bold.json'),'confounds':str(p/'conf.tsv'),'confounds_json':str(p/'conf.json')}
            with self.assertRaisesRegex(ValueError,'Confounds rows'):
                validate_run_inputs(run,'rest')

    def test_reader_includes_sessionless_and_session_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            write_json(p/'dataset_description.json',{'Name':'test','BIDSVersion':'1.9.0','DatasetType':'derivative',
                       'GeneratedBy':[{'Name':'DeepPrep','Version':'24.1.2'}]})
            write_json(p/'code/adapter/input_filter.json',{'bold':{'task':'rest','space':'MNI152NLin6Asym'},
                       'anat_to_template_xfm':{'extension':'.nii.gz'},'template_to_anat_xfm':{'extension':'.nii.gz'}})
            a=p/'sub-001/anat';a.mkdir(parents=True)
            for name in ['sub-001_desc-preproc_T1w','sub-001_space-MNI152NLin6Asym_desc-brain_mask',
                         'sub-001_from-T1w_to-MNI152NLin6Asym_mode-image_xfm',
                         'sub-001_from-MNI152NLin6Asym_to-T1w_mode-image_xfm']:
                nb.save(nb.Nifti1Image(np.ones((3,4,5)),np.eye(4)),a/(name+'.nii.gz'))
            for prefix,folder in [('sub-001_task-rest_run-01','sub-001/func'),
                                  ('sub-001_ses-B_task-rest_run-02','sub-001/ses-B/func')]:
                f=p/folder;f.mkdir(parents=True)
                stem=prefix+'_space-MNI152NLin6Asym_res-2'
                im=nb.Nifti1Image(np.ones((3,4,5,10)),np.eye(4));im.header.set_zooms((1,1,1,2))
                nb.save(im,f/(stem+'_desc-preproc_bold.nii.gz'))
                write_json(f/(stem+'_desc-preproc_bold.json'),{'RepetitionTime':2})
                for suffix in ['_desc-brain_mask','_boldref']:
                    nb.save(nb.Nifti1Image(np.ones((3,4,5)),np.eye(4)),f/(stem+suffix+'.nii.gz'))
                (f/(prefix+'_desc-confounds_timeseries.tsv')).write_text('trans_x\n'+'0\n'*10)
                write_json(f/(prefix+'_desc-confounds_timeseries.json'),{})
            self.assertEqual(reader_validate(p)[0]['runs'],2)

    def test_conflicting_work_files_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=Path(tmp)/'a',Path(tmp)/'b'
            a.write_text('first');b.write_text('other')
            with self.assertRaisesRegex(ValueError,'Ambiguous'):
                unique_file([a,b],'warp')
            b.write_text('first')
            self.assertEqual(unique_file([b,a],'warp'),str(a))

    def test_subject_path_injection_rejected(self):
        self.assertEqual(normalized_subject('001'),'sub-001')
        with self.assertRaises(ValueError):normalized_subject('../001')

    def test_workdir_fallback_sessions_and_run_boundaries(self):
        # Simulate a published BOLD tree without a warp: recover exact BIDS
        # filename from retained work, without choosing another participant.
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);b=p/'BOLD';w=p/'WorkDir';b.mkdir();w.mkdir()
            names=['sub-001_desc-preproc_T1w.nii.gz','sub-001_desc-brain_mask.nii.gz',
                   'sub-001_space-T1w_res-2mm_desc-skull_T1w.nii.gz',
                   'sub-001_space-MNI152NLin6Asym_res-02_desc-skull_T1w.nii.gz']
            for name in names:(b/name).write_text('fixture')
            warp=w/'sub-001_from-T1w_to-MNI152NLin6Asym_desc-joint_trans.nii.gz';warp.write_text('fixture')
            for prefix in ['sub-001_task-rest_run-01','sub-001_ses-B_task-rest_run-02','sub-001_task-resting_run-01']:
                for suffix in ['_space-MNI152NLin6Asym_res-2_desc-preproc_bold.nii.gz',
                               '_space-MNI152NLin6Asym_res-2_desc-preproc_bold.json',
                               '_desc-confounds_timeseries.tsv','_desc-confounds_timeseries.json',
                               '_space-T1w_desc-brain_mask.nii.gz']:
                    (b/(prefix+suffix)).write_text('fixture')
            manifest=p/'manifest.json'
            args=argparse.Namespace(deepprep_dir=w,search_root=[],subjects=['001'],
                                    space='MNI152NLin6Asym',resolution=2,task='rest',manifest=manifest)
            plan(args)
            record=json.loads(manifest.read_text())['subjects'][0]
            self.assertEqual(record['anatomy']['forward_ras'],str(warp))
            self.assertEqual(len(record['runs']),2)
            self.assertTrue(all(r['boldref'] is None for r in record['runs']))


if __name__=='__main__':unittest.main()
