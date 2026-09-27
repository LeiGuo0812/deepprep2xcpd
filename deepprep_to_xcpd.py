#!/usr/bin/env python3
"""Auditable DeepPrep SynthMorph -> XCP-D NIfTI adapter.

plan discovers exact BIDS-named files (including retained working directories).
convert consumes an explicit manifest; it never guesses among conflicting files.
validate exercises the installed XCP-D reader without starting its workflow.
See README.md for the format contract, limitations and Docker examples.
"""
import argparse
from contextlib import nullcontext
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import multiprocessing
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

import nibabel as nb
import numpy as np
import pandas as pd

from transforms import affine_points, apply, error_summary, field_image, inverse_field, sample
from compatibility import load_readers, reader_kwargs, source_generated_by

VERSION = '2.3.0'
PROFILE = 'deepprep-synthmorph-ras-mm'
SUPPORTED_PROFILES = (PROFILE, 'deepprep-24.1.2-synthmorph-ras-mm')
SPACES = ('MNI152NLin6Asym', 'MNI152NLin2009cAsym')
ANAT_ROLES = ('native_t1w', 'native_mask', 'registration_moving',
              'registration_warped', 'forward_ras')
RUN_ROLES = ('bold', 'bold_json', 'confounds', 'confounds_json', 't1w_mask')
BASE_CONFOUNDS = ['trans_x', 'trans_y', 'trans_z', 'rot_x', 'rot_y', 'rot_z',
                  'global_signal', 'white_matter', 'csf']
REQUIRED_36P = [b+s for b in BASE_CONFOUNDS
                for s in ('', '_derivative1', '_power2', '_derivative1_power2')]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(8*1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def same_grid(a, b):
    return a.shape[:3] == b.shape[:3] and np.allclose(a.affine, b.affine, atol=1e-4, rtol=0)


def image3d(path):
    im = nb.load(path)
    require(im.ndim == 3, f'Expected 3D image: {path}: {im.shape}')
    require(np.isfinite(im.affine).all() and abs(np.linalg.det(im.affine[:3,:3])) > 1e-8,
            f'Invalid affine: {path}')
    return im


def binary_mask(im):
    values = im.get_fdata()
    require(np.isin(values, [0, 1]).all() and np.any(values), 'Mask must be nonempty and binary')
    return values > 0


def normalized_subject(s):
    s = s.removeprefix('sub-')
    require(re.fullmatch('[A-Za-z0-9]+', s), f'Invalid subject label: {s}')
    return 'sub-'+s


def unique_file(paths, role):
    """Collapse symlinks/byte-identical duplicates; refuse conflicting candidates."""
    paths = sorted({Path(p).resolve() for p in paths if Path(p).is_file()})
    require(paths, f'Missing {role}; check published BOLD files, then retained work; specify an exact source path in the manifest')
    if len(paths) > 1:
        signatures = {(p.stat().st_size, digest(p)) for p in paths}
        require(len(signatures) == 1, f'Ambiguous {role}; explicitly select one:\n'+'\n'.join(map(str,paths)))
    return str(paths[0])


def plan(args):
    source = args.deepprep_dir.resolve()
    require(source.is_dir(), f'Missing source directory: {source}')
    if (source/'BOLD').is_dir() or (source/'WorkDir').is_dir():
        boldroot = source/'BOLD'
        workroot = source/'WorkDir'
    elif source.name == 'WorkDir':
        boldroot = source.parent/'BOLD'
        workroot = source
    else:
        boldroot = source
        workroot = source.parent/'WorkDir'
    subjects = list(dict.fromkeys(normalized_subject(s) for s in args.subjects))
    require(args.resolution > 0, 'Resolution must be positive')
    require(re.fullmatch('[A-Za-z0-9]+', args.task), 'Invalid task label')
    roots = [boldroot]+[p.resolve() for p in args.search_root]
    for root in roots[1:]:
        require(root.is_dir(), f'Missing --search-root directory: {root}')
    if workroot.is_dir():
        roots.append(workroot)
    # Do not follow directory symlinks into unrelated projects or crawl raw data.
    candidates = []
    def index_root(root):
        for parent, dirs, files in os.walk(root):
            # Avoid traversing other participants' large per-volume work trees.
            dirs[:] = [d for d in dirs if d != 'code' and not d.startswith('.')
                       and (not (m := re.match(r'^(sub-[A-Za-z0-9]+)(?:_|$)',d))
                       or m[1] in subjects)]
            for name in files:
                if any(name.startswith(s+'_') for s in subjects):
                    p = Path(parent)/name
                    if p.is_file():
                        candidates.append(p)
    index_root(boldroot)
    fallback_indexed = False
    def index_work():
        nonlocal fallback_indexed
        if not fallback_indexed:
            for root in dict.fromkeys(roots[1:]):
                index_root(root)
            fallback_indexed = True
    records = []
    for sid in subjects:
        def pick(pattern, role, optional=False):
            matches = [p for p in candidates if p.match(pattern)]
            if not matches:
                index_work()
                matches = [p for p in candidates if p.match(pattern)]
            # Published derivatives have priority over retained temporary copies.
            published = [p for p in matches if p.is_relative_to(boldroot)]
            if optional and not matches:
                return None
            return unique_file(published or matches, f'{sid}/{role}')
        anatomy = {
            'native_t1w': pick(f'{sid}_desc-preproc_T1w.nii.gz','native_t1w'),
            'native_mask': pick(f'{sid}_desc-brain_mask.nii.gz','native_mask'),
            'registration_moving': pick(f'{sid}_space-T1w_res-*_desc-skull_T1w.nii.gz','registration_moving'),
            'registration_warped': pick(f'{sid}_space-{args.space}_res-*_desc-skull_T1w.nii.gz','registration_warped'),
            'forward_ras': pick(f'{sid}_from-T1w_to-{args.space}_desc-joint_trans.nii.gz','forward_ras'),
        }
        regex = re.compile(rf'^{sid}_(?:[A-Za-z0-9]+-[A-Za-z0-9]+_)*space-{args.space}_res-0*{int(args.resolution)}_desc-preproc_bold\.nii\.gz$')
        # Work may already have been indexed to recover anatomy. That must not
        # silently add unpublished runs to a published acquisition selection.
        names = sorted({p.name for p in candidates if p.is_relative_to(boldroot) and regex.match(p.name)
                        and f'_task-{args.task}_' in p.name})
        if not names:
            index_work()
            names = sorted({p.name for p in candidates if regex.match(p.name)
                            and f'_task-{args.task}_' in p.name})
        require(names, f'No selected BOLD for {sid}, task={args.task}, space={args.space}, resolution={args.resolution}')
        runs = []
        for name in names:
            prefix = name.split('_space-')[0]
            require('_echo-' not in prefix, f'Multi-echo input requires a separately validated combination strategy: {name}')
            ent = name.removesuffix('.nii.gz').split('_')
            res = next(e.removeprefix('res-') for e in ent if e.startswith('res-'))
            boldpath = Path(pick(name, name))
            runs.append({
                'prefix': prefix, 'resolution': res,
                'bold': str(boldpath),
                'bold_json': pick(name.replace('.nii.gz','.json'), 'bold_json'),
                'confounds': pick(f'{prefix}_desc-confounds_timeseries.tsv','confounds'),
                'confounds_json': pick(f'{prefix}_desc-confounds_timeseries.json','confounds_json'),
                't1w_mask': pick(f'{prefix}_space-T1w_desc-brain_mask.nii.gz','t1w_mask'),
                'boldref': pick(f'{prefix}_space-{args.space}_res-{res}_boldref.nii.gz', 'boldref', optional=True),
            })
        records.append({'subject':sid, 'anatomy':anatomy, 'runs':runs})
    require(not args.manifest.exists(), f'Manifest already exists: {args.manifest}')
    manifest={'schema_version':1, 'profile':PROFILE,
              'space':args.space, 'task':args.task, 'subjects':records,
              'source_dataset':str(boldroot.resolve())}
    if getattr(args,'participants_tsv',None):
        require(args.participants_tsv.is_file(),'Missing participants TSV')
        manifest['participants_tsv']=str(args.participants_tsv.resolve())
    write_json(args.manifest,manifest)
    print(f'Published dataset: {boldroot}\nWrote {args.manifest}: {len(records)} subjects, {sum(len(r["runs"]) for r in records)} runs. No conversion executed.')


def validate_run_inputs(run, task):
    prefix = run['prefix']
    require(str(run['bold']).endswith('.nii.gz'), 'Copied BOLD input must be .nii.gz')
    require(f'_task-{task}_' in prefix+'_', f'Wrong task in {prefix}')
    require(re.fullmatch(r'sub-[A-Za-z0-9]+(?:_[A-Za-z0-9]+-[A-Za-z0-9]+)*', prefix), f'Invalid run prefix: {prefix}')
    require('_echo-' not in prefix and '_space-' not in prefix and '_desc-' not in prefix, 'Unsupported run prefix')
    require(re.fullmatch(r'[A-Za-z0-9]+',str(run['resolution'])), 'Invalid resolution entity')
    bold = nb.load(run['bold'])
    require(bold.ndim == 4 and bold.shape[3] > 1, f'Expected 4D BOLD: {run["bold"]}')
    meta = json.loads(Path(run['bold_json']).read_text())
    tr = meta.get('RepetitionTime')
    require(isinstance(tr,(int,float)) and np.isfinite(tr) and tr > 0, 'Missing/invalid JSON RepetitionTime')
    units = bold.header.get_xyzt_units()
    require(units[1] == 'sec', f'BOLD time units must be seconds: {units}')
    require(np.isclose(bold.header.get_zooms()[3], tr, rtol=1e-4), 'NIfTI/JSON TR disagreement')
    cf = pd.read_table(run['confounds'])
    require(len(cf) == bold.shape[3], f'Confounds rows {len(cf)} != BOLD frames {bold.shape[3]}')
    missing = sorted(set(REQUIRED_36P)-set(cf.columns))
    require(not missing, f'Missing 36P columns (will not fabricate): {missing}')
    for col in REQUIRED_36P:
        val = pd.to_numeric(cf[col], errors='raise').to_numpy(dtype=float)
        # Only the first derivative row may be undefined.
        valid = val[1:] if '_derivative1' in col else val
        require(np.isfinite(valid).all(), f'Invalid nuisance values: {col}')
        require(not np.isinf(val).any(), f'Infinite nuisance values: {col}')
    require(isinstance(json.loads(Path(run['confounds_json']).read_text()),dict), 'Invalid confounds JSON')
    return bold, float(tr)


def materialize(src, dest, mode):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if mode == 'copy':
        shutil.copy2(src, dest)
    else:
        dest.symlink_to(os.path.relpath(Path(src).resolve(), dest.parent))


def provenance(dest, sources, **extra):
    write_json(str(dest).replace('.nii.gz','.json'), {
        'Sources': [Path(p).resolve().as_uri() for p in sources],
        'GeneratedBy':[{'Name':'DeepPrepXCPDAdapter','Version':VERSION}], **extra})


def adapt_record(record, stage, space, task, mode):
    sid = normalized_subject(record['subject'])
    a = record['anatomy']
    out = stage/sid/'anat'
    out.mkdir(parents=True)
    scratch = stage/'code'/'adapter'/sid
    scratch.mkdir(parents=True)
    native = image3d(a['native_t1w'])
    nmask = image3d(a['native_mask'])
    require(all(str(a[k]).endswith('.nii.gz') for k in ('native_t1w','native_mask')),
            'Copied anatomical inputs must be .nii.gz; do not rename MGZ or uncompressed NIfTI')
    require(same_grid(native,nmask), 'Native T1/mask geometry differs; choose the correct registered pair')
    nativebrain = binary_mask(nmask)
    moving = image3d(a['registration_moving'])
    fixed = image3d(a['registration_warped'])
    warp = nb.load(a['forward_ras'])
    require(warp.shape in (fixed.shape+(3,), fixed.shape+(1,3)), f'Wrong displacement shape: {warp.shape}')
    require(same_grid(warp,fixed), 'Forward displacement must be sampled on the warped T1/template lattice')
    field = warp.get_fdata(dtype=np.float32).reshape(fixed.shape+(3,))
    require(np.isfinite(field).all(), 'Nonfinite source displacement')
    # Both volumes may have different grids, but must use the same native physical frame.
    forward = out/f'{sid}_from-T1w_to-{space}_mode-image_xfm.nii.gz'
    inverse = out/f'{sid}_from-{space}_to-T1w_mode-image_xfm.nii.gz'
    field_image(field,warp.affine,forward)
    provenance(forward,[a['forward_ras']], Description='RAS millimetre displacement converted to ITK LPS vector components; image-resampling transform from T1w to template. Coordinate pull map is template to T1w.')
    nativeout = out/f'{sid}_desc-preproc_T1w.nii.gz'
    materialize(a['native_t1w'],nativeout,mode)
    materialize(a['native_mask'],out/f'{sid}_desc-brain_mask.nii.gz',mode)
    provenance(nativeout,[a['native_t1w']])
    stdmask = out/f'{sid}_space-{space}_desc-brain_mask.nii.gz'
    apply(a['native_mask'],a['registration_warped'],stdmask,forward,'NearestNeighbor')
    brain = binary_mask(nb.load(stdmask))
    reproduced = scratch/'reproduced_T1w.nii.gz'
    apply(a['registration_moving'],a['registration_warped'],reproduced,forward)
    expected, actual = fixed.get_fdata()[brain],nb.load(reproduced).get_fdata()[brain]
    require(np.isfinite(expected).all() and np.isfinite(actual).all() and np.std(expected)>0 and np.std(actual)>0, 'Invalid T1 intensities')
    corr = float(np.corrcoef(expected,actual)[0,1])
    rmse = float(np.sqrt(np.mean((expected-actual)**2))/np.sqrt(np.mean(expected**2)))
    require(corr >= .9999 and rmse <= .001, f'Forward convention/source mismatch: r={corr}, relative RMSE={rmse}')
    gradient = np.stack([np.stack(np.gradient(field[...,i]),axis=-1) for i in range(3)],axis=-2)
    jac = np.linalg.det(gradient@np.linalg.inv(warp.affine[:3,:3])+np.eye(3))[brain]
    require(np.isfinite(jac).all() and np.min(jac)>0, 'Nonpositive/nonfinite finite-difference Jacobian in template brain; inverse not accepted')
    del gradient
    invfield,iterations = inverse_field(field,warp.affine,native,brain,nativebrain)
    field_image(invfield,native.affine,inverse)
    x = affine_points(warp.affine,np.argwhere(brain))
    y = x+field[brain]
    inverse_vox = affine_points(np.linalg.inv(native.affine),y)
    std_supported = np.all((inverse_vox>=0)&(inverse_vox<=np.array(native.shape)-1),axis=1)
    err_std = np.linalg.norm(y+sample(invfield,np.linalg.inv(native.affine),y)-x,axis=1)
    nativepoints = affine_points(native.affine,np.argwhere(nativebrain))
    back = nativepoints+invfield[nativebrain]
    vox = affine_points(np.linalg.inv(warp.affine),back)
    supported = np.all((vox>=0)&(vox<=np.array(fixed.shape)-1),axis=1)
    err_native = np.linalg.norm(back+sample(field,np.linalg.inv(warp.affine),back)-nativepoints,axis=1)
    stats = {'template_roundtrip':error_summary(err_std), 'native_roundtrip':error_summary(err_native),
             'native_supported_fraction':float(supported.mean()),
             'template_supported_fraction':float(std_supported.mean()),
             'jacobian_min':float(jac.min()), 'iterations_per_slab':iterations}
    write_json(scratch/'transform_metrics.json',stats)
    require(np.percentile(err_std,99)<=.5 and err_std.max()<=2 and err_native.max()<=.01
            and supported.mean()>=.999 and std_supported.mean()>=.999, f'Inverse quality gate failed: {stats}')
    rtmask = scratch/'roundtrip_native_mask.nii.gz'
    apply(stdmask,a['native_t1w'],rtmask,inverse,'NearestNeighbor')
    roundmask = binary_mask(nb.load(rtmask))
    dice = float(2*np.count_nonzero(roundmask&nativebrain)/(roundmask.sum()+nativebrain.sum()))
    require(dice>=.95, f'Mask roundtrip Dice too low: {dice}')
    provenance(inverse,[a['forward_ras']], Description='Numerical inverse of the same forward pull map; sampled on the native T1 lattice, ITK LPS vector mm.',
               ValidDomain='Brain with support in source grids; nearest boundary extension outside the field is not independently validated.',
               Validation=stats)
    provenance(stdmask,[a['native_mask'],a['forward_ras']], SpatialReference=space,Interpolation='NearestNeighbor')
    runresults=[]
    for run in record['runs']:
        prefix,res=run['prefix'],str(run['resolution'])
        require(prefix.startswith(sid+'_'), f'Cross-subject run: {prefix}')
        bold,tr=validate_run_inputs(run,task)
        ses=re.search(r'_ses-([A-Za-z0-9]+)(?:_|$)',prefix)
        func=stage/sid/(f'ses-{ses[1]}/func' if ses else 'func')
        func.mkdir(parents=True,exist_ok=True)
        base=f'{prefix}_space-{space}_res-{res}'
        for role,name in [('bold',base+'_desc-preproc_bold.nii.gz'),('bold_json',base+'_desc-preproc_bold.json'),
                          ('confounds',prefix+'_desc-confounds_timeseries.tsv'),('confounds_json',prefix+'_desc-confounds_timeseries.json')]:
            materialize(run[role],func/name,mode)
        refout=func/(base+'_boldref.nii.gz')
        if run.get('boldref'):
            ref=nb.load(run['boldref'])
            require(ref.ndim==3 or (ref.ndim==4 and ref.shape[3]==1), 'BOLD reference must be 3D or singleton 4D')
            require(same_grid(ref,bold), 'Reference/BOLD grid mismatch')
            data=np.asarray(ref.dataobj).reshape(ref.shape[:3])
            ref_sources=[run['boldref']]
            desc='Removed singleton time dimension if present; preserved spatial geometry and voxel values.'
        else:
            ref=bold
            data=np.asarray(bold.dataobj[...,0])
            ref_sources=[run['bold']]
            desc='First retained standard-space BOLD frame, following the declared SynthMorph RAS-mm profile reference convention.'
        header=ref.header.copy()
        ref3d=nb.Nifti1Image(data,ref.affine,header)
        ref3d.header.set_xyzt_units('mm')
        nb.save(ref3d,refout)
        provenance(refout,ref_sources,Description=desc)
        fmask=image3d(run['t1w_mask'])
        binary_mask(fmask)
        maskout=func/(base+'_desc-brain_mask.nii.gz')
        apply(run['t1w_mask'],refout,maskout,forward,'NearestNeighbor')
        mask=nb.load(maskout)
        binary_mask(mask)
        require(same_grid(mask,bold), 'Generated functional mask grid mismatch')
        provenance(maskout,[run['t1w_mask'],a['forward_ras']],SpatialReference=space,Interpolation='NearestNeighbor')
        runresults.append({'prefix':prefix,'frames':bold.shape[3],'TR':tr,'mask_voxels':int(binary_mask(mask).sum())})
    reproduced.unlink()
    rtmask.unlink()
    return {'subject':sid,'passed':True,'forward_correlation':corr,'forward_relative_RMSE':rmse,
            'inverse':stats,'mask_roundtrip_Dice':dice,'runs':runresults}


def reader_validate(root, subjects=None, expected_bold=None):
    """Exercise the installed reader by capability, without a release allowlist."""
    collect_data, collect_run_data = load_readers()
    from bids import BIDSLayout
    from bids.layout import Query
    filters=json.loads((root/'code'/'adapter'/'input_filter.json').read_text())
    root=Path(root).resolve()
    layout=BIDSLayout(root,validate=False,config=['bids','derivatives'])
    selection=root/'code/adapter/source_manifest.json'
    if not subjects and selection.is_file():
        subjects=[r['subject'] for r in json.loads(selection.read_text())['subjects']]
    subjects=subjects or layout.get_subjects()
    require(subjects,'No subjects found')
    results=[]
    for sid in subjects:
        label=normalized_subject(sid).removeprefix('sub-')
        sessions=layout.get_sessions(subject=label)+[Query.NONE]
        data=collect_data(**reader_kwargs(collect_data,
            dict(layout=layout,input_type='fmriprep',participant_label=label,
                 bids_filters=filters,file_format='nifti'),
            dict(anat_session=None,func_sessions=sessions)))
        require(isinstance(data,dict),'XCP-D collect_data must return a data dictionary; check the reader API')
        require(data['bold'],f'No collected BOLD for {label}')
        for key in ('anat_to_template_xfm','template_to_anat_xfm'):
            require(data[key].endswith('.nii.gz'),f'XCP-D selected wrong transform: {data[key]}')
        runs=[collect_run_data(**reader_kwargs(collect_run_data,
            dict(layout=layout,bold_file=bold,file_format='nifti',target_space=filters['bold']['space'])))
            for bold in data['bold']]
        expected=(expected_bold.get('sub-'+label) if expected_bold is not None else
                  layout.get(return_type='file',subject=label,datatype='func',suffix='bold',
                             desc='preproc',extension='.nii.gz',**filters['bold']))
        expected={str(root/p) if not Path(p).is_absolute() else str(p) for p in expected}
        require(expected==set(data['bold']),f'XCP-D selected different runs than requested: {label}')
        results.append({'subject':'sub-'+label,'runs':len(runs),'passed':True})
    return results


def output_root(args):
    output=args.output.resolve()
    if getattr(args,'layout','separate')=='inplace' and (output/'BOLD').is_dir():
        output=(output/'BOLD').resolve()
    return output


def expected_paths(records,space):
    result={}
    for record in records:
        sid=normalized_subject(record['subject']);paths=[]
        for run in record['runs']:
            prefix=run['prefix'];ses=re.search(r'_ses-([A-Za-z0-9]+)(?:_|$)',prefix)
            folder=Path(sid)/(f'ses-{ses[1]}/func' if ses else 'func')
            paths.append(str(folder/f'{prefix}_space-{space}_res-{run["resolution"]}_desc-preproc_bold.nii.gz'))
        result[sid]=paths
    return result


def merge_json_metadata(stagefile,original,dataset=False):
    new=json.loads(stagefile.read_text());old=json.loads(original.read_text())
    require(isinstance(old,dict),f'Expected metadata object: {original}')
    if dataset:
        require(old.get('DatasetType','derivative')=='derivative','Refusing to modify a raw BIDS dataset')
    merged=dict(old)
    for key,value in new.items():
        if key=='GeneratedBy':
            existing=list(old.get(key,[]))
            # Preserve source software entries; append this adapter version.
            for entry in value:
                if entry.get('Name')=='DeepPrepXCPDAdapter' and entry not in existing:
                    existing.append(entry)
            merged[key]=existing
        elif key=='Sources' and not dataset:
            merged[key]=list(dict.fromkeys(old.get(key,[])+value))
        elif key not in merged:
            merged[key]=value
    merged['DeepPrepXCPDAdapter']={'Version':VERSION,'Layout':'inplace'}
    write_json(stagefile,merged)


def prepare_inplace_metadata(stage,output,records,space):
    """Preserve original metadata and allow only a documented set of replacements."""
    replacements={'dataset_description.json','participants.tsv'}
    description=output/'dataset_description.json'
    if description.exists():
        merge_json_metadata(stage/'dataset_description.json',description,dataset=True)
    # Preserve all original rows/columns, including unselected participants.
    selected=pd.read_table(stage/'participants.tsv',dtype=str,keep_default_na=False)
    existing=output/'participants.tsv'
    old=(pd.read_table(existing,dtype=str,keep_default_na=False) if existing.exists()
         else pd.DataFrame({'participant_id':sorted(p.name for p in output.glob('sub-*') if p.is_dir())}))
    require('participant_id' in old and old.participant_id.is_unique,'Existing participants table has invalid IDs')
    columns=list(old.columns)+[c for c in selected.columns if c not in old.columns]
    all_ids=list(dict.fromkeys(list(old.participant_id)+list(selected.participant_id)))
    merged=old.reindex(columns=columns,fill_value='').set_index('participant_id').reindex(all_ids,fill_value='')
    for _,row in selected.iterrows():
        sid=row['participant_id']
        for col in selected.columns:
            if col=='participant_id':continue
            previous=merged.at[sid,col];new=row[col]
            require(previous in ('','n/a') or new in ('','n/a',previous),
                    f'Conflicting participant metadata for {sid}, column {col}')
            if previous in ('','n/a') and new not in ('','n/a'):
                merged.at[sid,col]=new
    # A table with only participant_id has no value columns; handle new IDs too.
    all_ids=list(dict.fromkeys(list(old.participant_id)+list(selected.participant_id)))
    merged=merged.reindex(all_ids,fill_value='')
    merged.reset_index().to_csv(stage/'participants.tsv',sep='\t',index=False)
    for record in records:
        sid=normalized_subject(record['subject'])
        metadata=[Path(sid)/'anat'/f'{sid}_desc-preproc_T1w.json']
        for rel in expected_paths([record],space)[sid]:
            ref=Path(rel.replace('_desc-preproc_bold.nii.gz','_boldref.nii.gz'))
            metadata.append(Path(str(ref).replace('.nii.gz','.json')))
            if (output/ref).is_file():
                before=nb.load(output/ref);after=nb.load(stage/ref)
                require(before.ndim==3 or (before.ndim==4 and before.shape[3]==1),
                        f'Existing boldref is not 3D/singleton: {ref}')
                require(same_grid(before,after) and np.array_equal(
                    np.asarray(before.dataobj).reshape(before.shape[:3]),np.asarray(after.dataobj)),
                    f'Existing boldref values differ; refusing replacement: {ref}')
                replacements.add(str(ref))
        for rel in metadata:
            if (output/rel).is_file():
                merge_json_metadata(stage/rel,output/rel)
                replacements.add(str(rel))
    marker=output/'code/adapter/validation.json'
    if marker.is_file() and json.loads(marker.read_text()).get('adapter_version'):
        replacements.update(str(p.relative_to(stage)) for p in (stage/'code/adapter').rglob('*') if p.is_file())
    return replacements


def convert(args):
    jobs=getattr(args,'jobs',1)
    require(type(jobs) is int and 1<=jobs<=16,'--jobs must be an integer between 1 and 16')
    inplace=getattr(args,'layout','separate')=='inplace'
    if inplace:
        from inplace import dataset_lock
        root=output_root(args)
        require(root.is_dir(),'In-place output must be an existing DeepPrep BOLD directory')
        guard=dataset_lock(root)
    else:
        guard=nullcontext()
    with guard:
        _convert(args)


def rollback_command(args):
    from inplace import rollback
    rollback(args.input,args.transaction)


def _adapt_timed(record, stage, space, task, mode):
    """One subject writes only its own staging directories, never the dataset."""
    start=time.monotonic()
    print(f'Adapting {record["subject"]} (PID {os.getpid()}) ...',flush=True)
    try:
        result=adapt_record(record,stage,space,task,mode)
    except Exception as exc:
        raise RuntimeError(f'{record["subject"]}: {exc}') from exc
    result['seconds']=round(time.monotonic()-start,1)
    result['worker_pid']=os.getpid()
    return result


def _stage_records(records, callargs, jobs, retain):
    if jobs==1:
        for record in records:
            retain(_adapt_timed(record,*callargs))
        return
    # Spawn avoids inheriting the parent's dataset lock and numerical runtime.
    # Bound submitted work so a failed subject cannot leave a whole cohort queued.
    pool=ProcessPoolExecutor(max_workers=jobs,mp_context=multiprocessing.get_context('spawn'))
    pending={}
    remaining=iter(records)
    try:
        for _ in range(min(jobs,len(records))):
            record=next(remaining)
            pending[pool.submit(_adapt_timed,record,*callargs)]=record['subject']
        while pending:
            done,_=wait(pending,return_when=FIRST_COMPLETED)
            # Observe all failures in this completed batch before submitting more.
            completed=[(future,future.result()) for future in done]
            for future,result in completed:
                pending.pop(future)
                retain(result)
            for _ in completed:
                record=next(remaining,None)
                if record is None:
                    break
                pending[pool.submit(_adapt_timed,record,*callargs)]=record['subject']
    finally:
        for future in pending:
            future.cancel()
        # Already-running workers finish in staging before the parent releases
        # its lock. No scientific files are published on failure or interruption.
        pool.shutdown(wait=True,cancel_futures=True)


def _convert(args):
    manifest_path=args.manifest.resolve()
    manifest=json.loads(manifest_path.read_text())
    require(manifest.get('schema_version')==1 and manifest.get('profile') in SUPPORTED_PROFILES,
            'Unsupported manifest schema or displacement convention; use the SynthMorph RAS-mm profile (legacy profile also accepted)')
    if manifest.get('source_dataset'):
        dataset=Path(manifest['source_dataset'])
        if not dataset.is_absolute():
            dataset=manifest_path.parent/dataset
        manifest['source_dataset']=str(dataset.resolve())
    require(manifest['space'] in SPACES,'Unsupported template space')
    require(re.fullmatch('[A-Za-z0-9]+',manifest['task']),'Invalid task label')
    records=manifest['subjects']
    require(records,'Empty subject selection')
    subjects=[normalized_subject(r['subject']) for r in records]
    require(len(set(subjects))==len(subjects),'Duplicate subjects in manifest')
    jobs=getattr(args,'jobs',1)
    effective_jobs=min(jobs,len(records))
    execution={'requested_jobs':jobs,'effective_jobs':effective_jobs,
               'start_method':'spawn' if effective_jobs>1 else 'serial',
               'thread_environment':{key:os.environ.get(key) for key in (
                   'ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS','OMP_NUM_THREADS',
                   'OPENBLAS_NUM_THREADS','MKL_NUM_THREADS')}}
    inputs=set()
    if manifest.get('source_dataset'):
        description=Path(manifest['source_dataset'])/'dataset_description.json'
        if description.is_file():
            inputs.add(description.resolve())
    for record in records:
        require(record['runs'],'Subject has no runs')
        keys=[(r['prefix'],str(r['resolution'])) for r in record['runs']]
        require(len(keys)==len(set(keys)),'Duplicate run output paths')
        require(len({k[0] for k in keys})==len(keys),'Select one BOLD resolution per run')
        for obj,roles in [(record['anatomy'],ANAT_ROLES)]+[(r,RUN_ROLES) for r in record['runs']]:
            for role in roles+ (('boldref',) if obj.get('boldref') else ()):
                p=Path(obj[role])
                if not p.is_absolute():
                    p=manifest_path.parent/p
                p=p.resolve()
                require(p.is_file(),f'Missing {role}: {p}')
                obj[role]=str(p)
                inputs.add(p)
    participants=pd.DataFrame({'participant_id':subjects})
    if manifest.get('participants_tsv'):
        p=Path(manifest['participants_tsv'])
        if not p.is_absolute():p=manifest_path.parent/p
        p=p.resolve()
        participants=pd.read_table(p,dtype=str,keep_default_na=False)
        require('participant_id' in participants and participants.participant_id.is_unique,
                'Participants table needs unique participant_id')
        require(set(subjects)<=set(participants.participant_id),'Selected subjects missing from participants table')
        participants=participants.set_index('participant_id').loc[subjects].reset_index()
        inputs.add(p)
        manifest['participants_tsv']=str(p)
    output=output_root(args)
    inplace=getattr(args,'layout','separate')=='inplace'
    if inplace:
        require(args.mode=='copy','--mode symlink is only for --layout separate; in-place reuses existing files automatically')
        require(output.is_dir(),'In-place output must be an existing DeepPrep BOLD directory')
        require(any(p.is_relative_to(output) for p in inputs),'In-place target does not contain any manifest sources')
        if manifest.get('source_dataset'):
            require(Path(manifest['source_dataset']).resolve()==output,'In-place target differs from manifest source_dataset')
    else:
        require(not output.exists(),f'Output exists; use a new output directory: {output}')
        require(not any(p.is_relative_to(output) for p in inputs),'Output cannot contain source files')
    require(shutil.which('antsApplyTransforms'),'antsApplyTransforms not found; use the XCP-D container')
    load_readers()
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.'+output.name+'.building-',dir=output.parent))
    reports=stage/'code'/'adapter'
    reports.mkdir(parents=True)
    phase='HASHING_SOURCES'
    try:
        write_json(reports/'execution.json',execution)
        print(f'{phase}: {len(inputs)} source files; reading SHA256 before conversion ...',flush=True)
        provenance_records=[{'path':str(p),'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(inputs)]
        write_json(reports/'source_manifest.json',manifest)
        write_json(reports/'source_checksums.json',provenance_records)
        write_json(stage/'dataset_description.json',{
            'Name':'DeepPrep derivatives adapted for XCP-D NIfTI processing',
            'BIDSVersion':'1.9.0','DatasetType':'derivative',
            'GeneratedBy':source_generated_by(manifest)+[{'Name':'DeepPrepXCPDAdapter','Version':VERSION}],
            'Description':'Format/geometry adaptation of the declared SynthMorph RAS-mm profile; not an fMRIPrep rerun.'})
        write_json(reports/'input_filter.json',{
            'bold':{'space':manifest['space'],'task':manifest['task']},
            'anat_to_template_xfm':{'extension':'.nii.gz'},
            'template_to_anat_xfm':{'extension':'.nii.gz'}})
        results=[]
        def retain(result):
            results.append(result)
            write_json(reports/(result['subject']+'.json'),result)
            print(f'PASS {result["subject"]}: {result["seconds"]} seconds ({len(results)}/{len(records)})',flush=True)
        phase='STAGING_SUBJECTS'
        print(f'{phase}: requested jobs={jobs}, effective jobs={effective_jobs}',flush=True)
        _stage_records(records,(stage,manifest['space'],manifest['task'],
                               'symlink' if inplace else args.mode),effective_jobs,retain)
        order={sid:i for i,sid in enumerate(subjects)}
        results.sort(key=lambda r:order[r['subject']])
        expected_bold=expected_paths(records,manifest['space'])
        phase='VALIDATING_READER'
        print(phase,flush=True)
        collected=reader_validate(stage,subjects,expected_bold)
        phase='RECHECKING_SOURCES'
        print(f'{phase}: verifying SHA256 before publication ...',flush=True)
        require(all(digest(r['path'])==r['sha256'] for r in provenance_records),'Source changed during conversion; output not published')
        require(not any(p.is_symlink() and not p.exists() for p in stage.rglob('*')),'Broken source links')
        write_json(reports/'validation.json',{'adapter_version':VERSION,'passed':True,
                  'scope':'Transform, geometry, 36P input and installed XCP-D reader validation; not a full XCP-D run.',
                  'mode':'reuse' if inplace else args.mode,'layout':'inplace' if inplace else 'separate',
                  'subjects':results,'reader':collected,'execution':execution,
                  'software':{n:importlib.metadata.version(n) for n in ['xcp_d','nibabel','numpy','scipy','pandas']}})
        participants.to_csv(stage/'participants.tsv',sep='\t',index=False)
        phase='PUBLISHING'
        print(phase,flush=True)
        if inplace:
            from inplace import publish
            replacements=prepare_inplace_metadata(stage,output,records,manifest['space'])
            publication=publish(stage,output,replacements,
                lambda root:reader_validate(root,subjects,expected_bold))
            shutil.rmtree(stage,ignore_errors=True)
            if stage.exists():
                print(f"Cleanup notice: unused staging directory retained: {stage}",file=sys.stderr)
            print(f'PASS: supplemented existing dataset: {output}\nTransaction: {publication["transaction"]}\nReused: {publication["reused"]}; written: {publication["written"]}. XCP-D was not run.')
        else:
            require(not output.exists(),'Output appeared during conversion; refusing to replace it')
            stage.rename(output)
            print(f'PASS: ready input dataset: {output}\nXCP-D was not run.')
    except BaseException as exc:
        write_json(reports/'FAILED.json',{'passed':False,'phase':phase,
                   'error_type':type(exc).__name__,'error':str(exc),'execution':execution})
        print(f'FAILED: retained diagnostic staging directory: {stage}',file=sys.stderr)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',action='version',version=VERSION)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('plan',help='Discover explicit source files; no image conversion')
    p.add_argument('--deepprep-dir',type=Path,required=True,
                   help='DeepPrep output root or BOLD dataset (recommended). WorkDir is a legacy entry that first checks sibling BOLD.')
    p.add_argument('--search-root',type=Path,action='append',default=[],
                   help='Additional fallback directory for missing BOLD files; repeatable. Standard sibling WorkDir is detected automatically.')
    p.add_argument('--subjects',nargs='+',required=True)
    p.add_argument('--task',default='rest')
    p.add_argument('--space',choices=SPACES,default=SPACES[0])
    p.add_argument('--resolution',type=int,default=2)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--participants-tsv',type=Path,help='Optional original participant table; preserve selected rows and mapping columns')
    p.set_defaults(func=plan)
    p=sub.add_parser('convert',help='Prepare a separate dataset or supplement an existing DeepPrep BOLD directory')
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--layout',choices=['separate','inplace'],default='separate',
                   help='separate: new output (default); inplace: supplement existing DeepPrep BOLD root with backups')
    p.add_argument('--mode',choices=['copy','symlink'],default='copy',
                   help='Materialization for separate layout only; inplace automatically reuses existing files')
    p.add_argument('--jobs',type=int,default=1,
                   help='Concurrent subject staging processes, 1-16 (default: 1); reader checks and publication remain serial')
    p.set_defaults(func=convert)
    p=sub.add_parser('rollback',help='Restore a recorded in-place transaction; preserve externally edited files')
    p.add_argument('--input',type=Path,required=True,help='The actual BOLD dataset root')
    p.add_argument('--transaction',required=True,help='ID from code/adapter/inplace-runs')
    p.set_defaults(func=rollback_command)
    p=sub.add_parser('validate',help='Rerun actual XCP-D reader only (no image processing)')
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--subjects',nargs='+')
    p.add_argument('--report',type=Path,required=True)
    p.set_defaults(func=lambda a:write_json(a.report,{'reader':reader_validate(a.input.resolve(),a.subjects),
                                                    'scope':'XCP-D reader only; transform quality is recorded at conversion.'}))
    args=parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        parser.exit(1,f'{type(exc).__name__}: {exc}\n')


if __name__=='__main__':
    main()
