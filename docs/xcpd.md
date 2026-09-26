# 交给 XCP-D 后处理

[返回首页](../README.md)

本工具只准备输入。以下是 **XCP-D 26.2.0 NIfTI 单被试示例**，不是对所有研究的默认分析建议。准备好的输入使用 `--input-type fmriprep` 兼容读取，但其生成软件仍是 DeepPrep。

## 输入与过滤器

- 独立模式：挂载生成的 `xcpd-input`。
- inplace 模式：挂载已经补齐的原 `BOLD`，而非它的 DeepPrep 父目录。
- symlink 模式：还要挂载所有链接目标并保持路径可解析；仅挂载输入目录可能断链。
- 使用输入中的 `code/adapter/input_filter.json`，它选定 task/space，并将变换扩展名限定为 `.nii.gz`，避免同名 JSON 被当成变换。
- inplace 多次适配时，根过滤器对应最近一次任务选择；历史过滤器在事务 `records/` 中。后处理应选择已完成适配的被试和对应过滤器。

## 一套可修改的分析示例

以下演示去前 5 帧、despike、36P（含全局信号回归）、0.01–0.08 Hz、6 mm 平滑、不按 FD 删除帧，并使用内置 Glasser 体积分区。图谱可用性和自定义设置参照 [XCP-D 26.2.0 使用文档](https://xcp-d.readthedocs.io/en/26.2.0/usage.html)。

```bash
export XCPD_INPUT='/absolute/path/to/adapter-results/xcpd-input'
export XCPD_OUTPUT='/absolute/path/to/xcpd-output'
export XCPD_WORK='/absolute/path/to/xcpd-work'
mkdir -p "$XCPD_OUTPUT" "$XCPD_WORK"

docker run --rm --init \
  -e DISABLE_SQLALCHEMY_CEXT_RUNTIME=1 \
  -e ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=2 \
  -e OPENBLAS_NUM_THREADS=1 \
  -v "$XCPD_INPUT:/input:ro" \
  -v "$XCPD_OUTPUT:/output" \
  -v "$XCPD_WORK:/work" \
  pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca \
  /input /output participant --participant-label 001 \
  --bids-filter-file /input/code/adapter/input_filter.json \
  --mode linc --input-type fmriprep --file-format nifti --task-id rest \
  --nuisance-regressors 36P --despike y --dummy-scans 5 \
  --lower-bpf 0.01 --upper-bpf 0.08 --bpf-order 2 \
  --motion-filter-type none --head-radius 50 \
  --fd-thresh 0 --censor-between 0 --min-time 0 --smoothing 6 \
  --atlases Glasser --min-coverage 0.5 \
  --combine-runs n --output-type censored --output-run-wise-correlations y \
  --linc-qc y --abcc-qc n --report-output-level subject --output-layout bids \
  --warp-surfaces-native2std n --nprocs 8 --omp-nthreads 2 --mem-mb 32000 \
  --notrack -w /work
```

这是后处理 Docker 命令，不经过 `docker_adapter.sh`。该命令按 Docker 默认用户运行；若 Linux 输出所有权需要跟随当前用户，可在 `docker run` 中增加 `--user "$(id -u):$(id -g)"`，并确保缓存目录可写。

初次 XCP-D 运行可能下载 TemplateFlow 资源；需配置可访问的网络或预先准备的缓存。适配器的读取测试不等同于该完整后处理命令已经在你的设备上跑通。

## 参数边界

- `--dummy-scans 5` 仅由 XCP-D 执行；适配器本身保留所有 BOLD 时间点。
- FD=0 不因 FD 额外删帧；不要将输出类型 `censored` 误解为一定发生了额外删帧。
- `--jobs` 是适配器的并发，`--nprocs` / `--omp-nthreads` 是 XCP-D 的资源选项。
- 内置 `Glasser` 不等于任意用户提供的 Glasser360 体积版。BN246、AAL3、自备 Glasser360 需要另外完成模板匹配、标签表和 atlas 数据集配置，仓库不含这些图谱。
- 6 mm 是平滑输出设置；流程中的 FC/ReHo 仍使用其未平滑分支。
- 此示例不生成所有可选 XCP-D 结果，例如 CIFTI/表面分支，也不提供 fALFF 计算。输出定义见 [XCP-D 26.2.0 输出文档](https://xcp-d.readthedocs.io/en/26.2.0/outputs.html)。
