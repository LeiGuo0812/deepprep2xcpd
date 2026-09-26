"""In-place publication safety and real ANTs/XCP-D reader integration."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest

import nibabel as nb
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deepprep_to_xcpd import (convert, plan, write_json, REQUIRED_36P, PROFILE,
                             prepare_inplace_metadata, reader_validate)
from inplace import publish, rollback, dataset_lock, sha


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'BOLD';self.stage=Path(self.tmp.name)/'stage'
        self.root.mkdir();(self.stage/'code/adapter').mkdir(parents=True)
        write_json(self.stage/'code/adapter/validation.json',{'adapter_version':'2.1.0'})

    def test_validation_failure_restores_original_and_removes_additions(self):
        (self.root/'existing').write_text('original')
        (self.stage/'existing').write_text('changed');(self.stage/'new').write_text('new')
        def fail(_):raise ValueError('reader rejected combined dataset')
        with dataset_lock(self.root),self.assertRaisesRegex(ValueError,'reader rejected'):
            publish(self.stage,self.root,{'existing'},fail)
        self.assertEqual((self.root/'existing').read_text(),'original')
        self.assertFalse((self.root/'new').exists())
        j=json.loads(next((self.root/'code/adapter/inplace-runs').glob('*/journal.json')).read_text())
        self.assertEqual(j['state'],'ROLLED_BACK')

    def test_conflict_found_before_any_publication(self):
        (self.root/'z-protected').write_text('original')
        (self.stage/'z-protected').write_text('different');(self.stage/'a-new').write_text('new')
        with self.assertRaisesRegex(ValueError,'Conflicting existing'):
            publish(self.stage,self.root,set(),lambda _:[])
        self.assertFalse((self.root/'a-new').exists())
        self.assertEqual((self.root/'z-protected').read_text(),'original')

    def test_reuse_symlink_then_explicit_rollback(self):
        original=self.root/'image';original.write_text('unchanged')
        (self.stage/'image').symlink_to(original)
        (self.stage/'addition').write_text('new')
        before=original.stat().st_mtime_ns
        with dataset_lock(self.root):result=publish(self.stage,self.root,set(),lambda _:['passed'])
        self.assertEqual(result['reused'],1)
        self.assertEqual(original.stat().st_mtime_ns,before)
        self.assertFalse((self.root/'addition').is_symlink())
        rollback(self.root,result['transaction'])
        self.assertFalse((self.root/'addition').exists());self.assertEqual(original.read_text(),'unchanged')

    def test_rollback_preserves_externally_edited_file(self):
        (self.stage/'addition').write_text('new')
        result=publish(self.stage,self.root,set(),lambda _:[])
        (self.root/'addition').write_text('user edit')
        with self.assertRaisesRegex(RuntimeError,'outside this transaction'):
            rollback(self.root,result['transaction'])
        self.assertEqual((self.root/'addition').read_text(),'user edit')

    def test_symlinked_directory_and_second_writer_rejected(self):
        outside=Path(self.tmp.name)/'outside';outside.mkdir()
        (self.root/'sub-001').symlink_to(outside,target_is_directory=True)
        (self.stage/'sub-001').mkdir();(self.stage/'sub-001/file').write_text('new')
        with self.assertRaisesRegex(ValueError,'symlinked destination'):
            publish(self.stage,self.root,set(),lambda _:[])
        with dataset_lock(self.root),self.assertRaisesRegex(ValueError,'holds the dataset lock'):
            with dataset_lock(self.root):pass


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'BOLD'
        a=self.root/'sub-001/anat';f=self.root/'sub-001/ses-01/func'
        a.mkdir(parents=True);f.mkdir(parents=True)
        shape=(14,15,16);aff=np.diag([2.,2.,2.,1.]);aff[:3,3]=[-14,-14,-14]
        ramp=(np.indices(shape).sum(axis=0)+1).astype('float32')
        mask=np.zeros(shape,dtype='uint8');mask[3:-3,3:-3,3:-3]=1
        def img(path,data):nb.save(nb.Nifti1Image(data,aff),path);return str(path)
        anatomy={}
        for role,name,data in [
            ('native_t1w','sub-001_desc-preproc_T1w',ramp),
            ('native_mask','sub-001_desc-brain_mask',mask),
            ('registration_moving','sub-001_space-T1w_res-2mm_desc-skull_T1w',ramp),
            ('registration_warped','sub-001_space-MNI152NLin6Asym_res-02_desc-skull_T1w',ramp),
            ('forward_ras','sub-001_from-T1w_to-MNI152NLin6Asym_desc-joint_trans',np.zeros(shape+(3,),dtype='float32'))]:
            anatomy[role]=img(a/(name+'.nii.gz'),data)
        write_json(a/'sub-001_desc-preproc_T1w.json',{'OriginalField':'preserve me'})
        prefix='sub-001_ses-01_task-rest_run-01';stem=prefix+'_space-MNI152NLin6Asym_res-2'
        bold=nb.Nifti1Image(np.repeat(ramp[...,None],10,axis=3),aff)
        bold.header.set_xyzt_units('mm','sec');bold.header.set_zooms((2,2,2,2))
        nb.save(bold,f/(stem+'_desc-preproc_bold.nii.gz'))
        write_json(f/(stem+'_desc-preproc_bold.json'),{'RepetitionTime':2,'OriginalField':42})
        conf=f/(prefix+'_desc-confounds_timeseries.tsv')
        pd.DataFrame(np.zeros((10,36)),columns=REQUIRED_36P).to_csv(conf,sep='\t',index=False)
        write_json(conf.with_suffix('.json'),{})
        run={'prefix':prefix,'resolution':'2','bold':str(f/(stem+'_desc-preproc_bold.nii.gz')),
             'bold_json':str(f/(stem+'_desc-preproc_bold.json')),'confounds':str(conf),
             'confounds_json':str(conf.with_suffix('.json')),
             't1w_mask':img(f/(prefix+'_space-T1w_desc-brain_mask.nii.gz'),mask),
             'boldref':img(f/(stem+'_boldref.nii.gz'),ramp[...,None])}
        self.ref=Path(run['boldref']);self.bold=Path(run['bold']);self.original_ref=sha(self.ref)
        write_json(self.root/'dataset_description.json',{'Name':'Original DeepPrep','BIDSVersion':'1.4.0',
                   'DatasetType':'derivative','GeneratedBy':[{'Name':'DeepPrep','Version':'24.1.2'}],
                   'HowToAcknowledge':'keep original citation'})
        (self.root/'participants.tsv').write_text('participant_id\toriginal_name\nsub-001\toriginal-one\nsub-999\tunselected\n')
        (self.root/'sub-999').mkdir();(self.root/'sub-999/keep.txt').write_text('unselected')
        # Other tasks/spaces stay visible in the raw tree, but must not be selected.
        nb.save(bold,f/(stem.replace('task-rest','task-food')+'_desc-preproc_bold.nii.gz'))
        nb.save(bold,f/(stem.replace('MNI152NLin6Asym','T1w')+'_desc-preproc_bold.nii.gz'))
        self.manifest=self.base/'manifest.json'
        write_json(self.manifest,{'schema_version':1,'profile':PROFILE,'source_dataset':str(self.root),
                   'space':'MNI152NLin6Asym','task':'rest','subjects':[{'subject':'sub-001','anatomy':anatomy,'runs':[run]}]})

    def test_full_conversion_inplace_preserves_reuses_reads_and_rolls_back(self):
        before={str(p.relative_to(self.root)):sha(p) for p in self.root.rglob('*') if p.is_file()}
        args=argparse.Namespace(manifest=self.manifest,output=self.root,layout='inplace',mode='copy')
        convert(args)
        self.assertEqual(nb.load(self.ref).ndim,3)
        self.assertEqual(sha(self.bold),before[str(self.bold.relative_to(self.root))])
        self.assertEqual((self.root/'sub-999/keep.txt').read_text(),'unselected')
        self.assertIn('original-one',(self.root/'participants.tsv').read_text())
        self.assertIn('unselected',(self.root/'participants.tsv').read_text())
        d=json.loads((self.root/'dataset_description.json').read_text())
        self.assertEqual(d['Name'],'Original DeepPrep');self.assertEqual(d['HowToAcknowledge'],'keep original citation')
        self.assertEqual(reader_validate(self.root,['001'])[0]['runs'],1)
        self.assertEqual([r['subject'] for r in reader_validate(self.root)],['sub-001'])
        journal=next((self.root/'code/adapter/inplace-runs').glob('*/journal.json'))
        j=json.loads(journal.read_text());self.assertEqual(j['state'],'COMMITTED')
        # Planning after in-place publication must not pick up backup copies.
        plan(argparse.Namespace(deepprep_dir=self.root,search_root=[],subjects=['001'],
             task='rest',space='MNI152NLin6Asym',resolution=2,manifest=self.base/'replan.json'))
        # A repeated execution is valid, then revert newest before oldest.
        # source_dataset follows the same manifest-relative path convention as files.
        manifest=json.loads(self.manifest.read_text())
        manifest['source_dataset']=str(self.root.relative_to(self.manifest.parent))
        write_json(self.manifest,manifest)
        convert(args)
        newest=next(p for p in (self.root/'code/adapter/inplace-runs').glob('*/journal.json') if p!=journal)
        for p in (newest,journal):rollback(self.root,p.parent.name)
        for rel,checksum in before.items():self.assertEqual(sha(self.root/rel),checksum,rel)
        self.assertEqual(nb.load(self.ref).ndim,4)

    def test_separate_copy_remains_independent_and_original_reference_unchanged(self):
        output=self.base/'separate'
        convert(argparse.Namespace(manifest=self.manifest,output=output,layout='separate',mode='copy'))
        self.assertEqual(sha(self.ref),self.original_ref)
        self.assertEqual(nb.load(output/self.ref.relative_to(self.root)).ndim,3)
        self.assertFalse(any(p.is_symlink() for p in output.rglob('*')))
        self.assertEqual(reader_validate(output,['001'])[0]['runs'],1)


if __name__=='__main__':unittest.main()
