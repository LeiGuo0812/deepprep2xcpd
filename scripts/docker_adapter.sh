#!/usr/bin/env bash
# Run the adapter in the tested XCP-D environment (Linux, WSL, or macOS Bash).
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
image="${ADAPTER_IMAGE:-pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca}"
if (($# == 0)); then
  set -- --help
fi
command_name=$1
source_access=ro
previous=''
for argument in "$@"; do
  if [[ "$command_name" == convert && ( "$argument" == --layout=inplace || ( "$previous" == --layout && "$argument" == inplace ) ) ]]; then
    source_access=rw
  fi
  previous=$argument
done
if [[ "$command_name" == rollback ]]; then
  source_access=rw
fi
run=(docker run --rm --init
  -e PYTHONDONTWRITEBYTECODE=1
  -e DISABLE_SQLALCHEMY_CEXT_RUNTIME=1
  -e "ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=${ADAPTER_ITK_THREADS:-2}"
  -e "OMP_NUM_THREADS=${ADAPTER_OMP_THREADS:-2}"
  -e "OPENBLAS_NUM_THREADS=${ADAPTER_BLAS_THREADS:-1}"
  -e "MKL_NUM_THREADS=${ADAPTER_BLAS_THREADS:-1}"
  -v "$repo_dir:/adapter:ro")
if [[ -n "${ADAPTER_PLATFORM:-}" ]]; then
  run+=(--platform "$ADAPTER_PLATFORM")
fi
# Use the calling user on Linux/WSL; do not leave root-owned output files.
if [[ "$(uname -s)" == Linux ]]; then
  run+=(--user "$(id -u):$(id -g)")
fi
mount_directory() {
  local host_dir=$1 container_dir=$2 access=$3
  if [[ ! -d "$host_dir" ]]; then
    echo "Directory does not exist: $host_dir" >&2
    exit 2
  fi
  host_dir=$(cd -- "$host_dir" && pwd -P)
  run+=(-v "$host_dir:$container_dir:$access")
}
if [[ "$command_name" == test ]]; then
  run+=(--network none --entrypoint python "$image" -m unittest discover -s /adapter/tests -v)
  exec "${run[@]}"
fi
case "$command_name" in
  plan|convert|validate|rollback)
    : "${ADAPTER_RESULTS:?Set ADAPTER_RESULTS to an existing writable results directory}"
    mount_directory "$ADAPTER_RESULTS" /result rw
    ;;
esac
if [[ -n "${DEEPPREP_DIR:-}" ]]; then
  mount_directory "$DEEPPREP_DIR" /deepprep "$source_access"
fi
if [[ -n "${BIDS_DIR:-}" ]]; then
  mount_directory "$BIDS_DIR" /bids ro
fi
if [[ -n "${EXTRA_WORK_DIR:-}" ]]; then
  mount_directory "$EXTRA_WORK_DIR" /extra_work ro
fi
run+=(--entrypoint python "$image" /adapter/deepprep_to_xcpd.py "$@")
exec "${run[@]}"
