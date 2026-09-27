# deepprep2xcpd 2.3.0

Adapt **DeepPrep SynthMorph RAS-displacement NIfTI derivatives** into an input dataset readable by **XCP-D**.

The adapter reuses preprocessed BOLD and confounds, converts displacement-vector conventions, computes and validates a numerical inverse, and supplies masks and 3D references. It does not rerun DeepPrep or perform XCP-D denoising.

## Quick start

Use Bash on Linux, WSL 2, or macOS with a working Docker Linux-container runtime:

```bash
git clone https://github.com/LeiGuo0812/deepprep2xcpd.git
cd deepprep2xcpd
export DEEPPREP_DIR='/absolute/path/to/deepprep_output'
export ADAPTER_RESULTS='/absolute/path/to/adapter-results'
mkdir -p "$ADAPTER_RESULTS"

bash scripts/docker_adapter.sh plan \
  --deepprep-dir /deepprep/BOLD --subjects 001 --task rest \
  --space MNI152NLin6Asym --resolution 2 --manifest /result/manifest.json

bash scripts/docker_adapter.sh convert \
  --manifest /result/manifest.json --output /result/xcpd-input \
  --layout separate --mode copy --jobs 1

bash scripts/docker_adapter.sh validate \
  --input /result/xcpd-input --subjects 001 --report /result/reader_validation.json
```

The launcher defaults to a tested XCP-D image (override it with `ADAPTER_IMAGE` to use another release) and downloads it on first Docker use if necessary. No GPU is required. Host paths are environment variables; command arguments use the container paths `/deepprep` and `/result`.

## Modes and parallelism

- `--layout separate --mode copy`: new, self-contained dataset; output must not already exist.
- `--layout separate --mode symlink`: save disk space but retain all source-link dependencies.
- `--layout inplace --output /deepprep/BOLD`: supplement the existing BOLD dataset with backups and transactional rollback.
- `--jobs N`: 1–16 concurrent subject-staging processes, default 1, capped at the selected subject count. Checksums, reader validation, and publication remain in the parent. This is not XCP-D's `--nprocs`.

Published BOLD sources have priority. WorkDir is only a fallback for missing sources and is not required when all necessary files are already published. Existing published runs prevent automatic inclusion of extra unpublished working-directory runs.

## Scope

Compatibility depends on SynthMorph joint physical RAS millimetre displacements, the installed XCP-D NIfTI reader API, and one shared anatomical registration per subject. Software versions are not allowlisted. DeepPrep 24.1.2 / XCP-D 26.2.0 are the historical full-validation environment, not mandatory releases. New manifests use `deepprep-synthmorph-ras-mm`; the old versioned profile remains an accepted alias. Source software versions are preserved from dataset_description.json and are never guessed. Independent session-specific T1 registrations, multi-echo combination, CIFTI, and surface reconstruction remain outside the current scope.

Validation uses actual ANTs and XCP-D reader operations on synthetic data in an x86_64 Linux container. macOS hardware, ARM emulation, and Apptainer instructions are provided as configuration guidance, not as claims of tested scientific equivalence on those platforms.

## Detailed documentation

The full operational documentation is in Chinese:

- [Chinese README and quick start](README.md)
- [Input contract, algorithms, CLI and rollback](docs/usage.md)
- [Platforms and environment](docs/platforms.md)
- [Migration, offline use and reproducibility](docs/migration.md)
- [XCP-D handoff](docs/xcpd.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Validation evidence](validation/README.md)

Run all synthetic and launcher tests with `bash scripts/docker_adapter.sh test`. Supply `code/adapter/input_filter.json` to XCP-D when using the adapted dataset. Successful reader validation is not a complete BIDS Validator run or a replacement for visual registration QC.
