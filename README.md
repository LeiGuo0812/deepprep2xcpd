# deepprep2xcpd

将 **DeepPrep** 的 NIfTI 输出整理为 **XCP-D** 可读取的输入，支持创建独立目录、在原 BOLD 目录补齐，以及逐被试并行转换。

**当前版本：2.3.0。** 本工具复用预处理 BOLD 和 confounds，转换原有 SynthMorph 位移场、生成数值逆变换、补建掩膜和三维参考图。它不重跑 DeepPrep，也不执行 XCP-D 的回归、滤波、平滑或删帧。

English overview: [README.en.md](README.en.md).

## 文档导航

| 内容 | 文档 |
|---|---|
| 输入要求、源文件选择、全部命令、算法与回滚 | [完整使用指南](docs/usage.md) |
| Linux、Windows/WSL、macOS、Apptainer、无 Docker 环境 | [平台与环境](docs/platforms.md) |
| 更换电脑、离线转移、容器路径、copy/symlink 差异 | [迁移与复现](docs/migration.md) |
| 如何把准备后的数据交给 XCP-D | [XCP-D 接入示例](docs/xcpd.md) |
| 常见错误与恢复 | [排错](docs/troubleshooting.md) |
| 版本变化 | [CHANGELOG.md](CHANGELOG.md) |
| 测试范围、运行环境和核查结果 | [验证说明](validation/README.md) |

## 适用范围

- **输入契约：** DeepPrep SynthMorph joint，目标模板格点上的物理 RAS 毫米位移场。
- **目标契约：** XCP-D NIfTI，使用 `--input-type fmriprep` 兼容读取；来源仍如实记录为 DeepPrep。
- 已验证空间为 `MNI152NLin6Asym`；CLI 也接受 `MNI152NLin2009cAsym`，但本发布未对后者完成真实数据验证。
- 支持任意合法被试标签、任务名称、多个 run，以及有/无 session 的功能数据；每名被试必须共用一套匹配的 T1 配准。
- 不自动组合多回波，不生成 CIFTI、皮层表面或缺失的真实混杂变量。不同 session 独立 T1 配准需要扩展输入绑定，不能直接混用。
- 已验证读取所需的 36P 列；不能据此认为任意 XCP-D 去噪策略的输入都已齐备。

软件版本号不作为准入条件。历史完整验证环境为 DeepPrep 24.1.2 / XCP-D 26.2.0；其他版本只要满足相同数据约定并通过当前读取检查即可使用。新 manifest 使用无版本 profile，旧 manifest 仍可直接使用。来源软件版本从源 dataset_description.json 保留，缺失时不编造。

## 快速开始：Linux / Windows WSL / macOS Bash

先安装 Git 和可运行 Linux 容器的 Docker。Windows 请进入 WSL 终端执行；平台差异见[平台文档](docs/platforms.md)。只需 CPU，不要求 GPU。

### 1. 获取代码与镜像

```bash
git clone https://github.com/LeiGuo0812/deepprep2xcpd.git
cd deepprep2xcpd

docker pull pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca
bash scripts/docker_adapter.sh --version
```

包装脚本默认使用已验证镜像以便复现；可以设置 `ADAPTER_IMAGE` 选择其他 XCP-D 镜像（tag 或 digest），例如 `export ADAPTER_IMAGE=pennlinc/xcp_d:latest`。选择其他版本不会被版本号检查拦截，仍须通过相同数据和读取检查。首次需要下载镜像；本工具没有另行构建自己的 Docker 镜像。Apple Silicon 的说明见[macOS 部分](docs/platforms.md#macos)。

### 2. 设置宿主机路径

```bash
export DEEPPREP_DIR='/absolute/path/to/deepprep_output'
export ADAPTER_RESULTS='/absolute/path/to/adapter-results'
mkdir -p "$ADAPTER_RESULTS"
```

`DEEPPREP_DIR` 应为包含 `BOLD` 的 DeepPrep 输出根目录：

```text
deepprep_output/
├── BOLD/sub-001/...
├── WorkDir/...       # 可选，仅在缺少来源时使用
└── Recon/...
```

脚本将代码挂载为 `/adapter`、DeepPrep 根目录挂载为 `/deepprep`、结果目录挂载为 `/result`。接下来传给 Python 的参数必须使用这些**容器路径**。包含空格的宿主机路径须像上面一样加引号。

### 3. 先为一名被试生成清单

```bash
bash scripts/docker_adapter.sh plan \
  --deepprep-dir /deepprep/BOLD \
  --subjects 001 --task rest \
  --space MNI152NLin6Asym --resolution 2 \
  --manifest /result/manifest.json
```

检查 `$ADAPTER_RESULTS/manifest.json` 中的来源和 run 是否正确。`BOLD` 中已发布文件优先；仅缺失所需文件时才查 WorkDir。已有已发布 BOLD 时，不自动混入工作目录中的额外 run。工作目录不是必需入口。

### 4. 二选一：独立输出或原目录补齐

**A. 独立目录（默认，便于跨设备搬移）：**

```bash
bash scripts/docker_adapter.sh convert \
  --manifest /result/manifest.json \
  --output /result/xcpd-input \
  --layout separate --mode copy --jobs 1
```

`$ADAPTER_RESULTS/xcpd-input` 必须尚不存在。源目录只读，生成的数据集可以独立搬移。

**B. 原 BOLD 目录补齐（节省大文件副本）：**

```bash
bash scripts/docker_adapter.sh convert \
  --manifest /result/manifest.json \
  --output /deepprep/BOLD \
  --layout inplace --jobs 1
```

包装脚本仅在 `convert --layout inplace` 和 `rollback` 时把 DeepPrep 挂载为可写。原目录模式复用已有 BOLD/T1/confounds，对允许的兼容修正先备份，并在一次事务内发布。不要在 DeepPrep 或下游 XCP-D 同时读写此目录时操作。

### 5. 检查结果，再交给 XCP-D

```bash
bash scripts/docker_adapter.sh validate \
  --input /result/xcpd-input \
  --subjects 001 --report /result/reader_validation.json
```

inplace 模式将 `--input` 改为 `/deepprep/BOLD`。`convert` 已执行变换和读取检查，这个命令用于单独复查读取。

最终 XCP-D 输入为新建的 `xcpd-input`，或已经补齐的原 `BOLD`。必须将其中的 `code/adapter/input_filter.json` 传给 XCP-D。具体命令见 [XCP-D 接入](docs/xcpd.md)。

## 多人并行

重新创建一个包含多个被试的 manifest，例如 `--subjects 001 002 003`，转换时用 `--jobs 2`。

- 默认 `--jobs 1`；允许 `1–16`，实际并发不超过清单被试数。
- 仅被试暂存转换并行；SHA256、读取验证和最终发布仍由父进程串行完成。
- `--jobs` 不等于 XCP-D 的 `--nprocs`，也不会启动后处理。
- 完整逆场可能占用较多内存；工具不自动估算内存。先测少量被试，再决定并发数。
- 并发、线程环境、PID 与耗时会写入结果的 `code/adapter/`；失败时不发布部分被试作为整批成功结果。

## 可复现测试

```bash
bash scripts/docker_adapter.sh test
```

测试只使用临时合成数据，包括真实 ANTs 运算、XCP-D 读取、双进程转换、失败保护和回滚。平台测试状态见[验证说明](validation/README.md)。读取器验证通过不等于完整 BIDS Validator 通过，也不能替代配准的视觉 QC。

## 仓库内容

```text
.
├── deepprep_to_xcpd.py     # plan / convert / validate / rollback
├── transforms.py          # RAS/LPS 变换及数值逆
├── inplace.py             # 锁、事务、备份及回滚
├── scripts/docker_adapter.sh
├── examples/manifest_template.json
├── docs/
├── tests/
├── validation/
└── SHA256SUMS.json
```

仓库只包含通用代码、示例和合成测试；影像、被试信息、FreeSurfer license 及真实项目工作目录由用户在本地提供。
