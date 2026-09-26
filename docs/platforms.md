# 平台与运行环境

[返回首页](../README.md) · [迁移数据](migration.md)

## 支持状态

代码的输入契约固定为 DeepPrep 24.1.2 / XCP-D 26.2.0；“可使用某种容器运行方式”不等于该硬件平台已经完成科学结果一致性验收。

| 平台 | 使用方式 | 本次状态 |
|---|---|---|
| Linux x86_64 | Docker Linux 容器 | 发布测试在 x86_64 Linux 容器中通过 |
| Windows + WSL 2 | 在 WSL Bash 中使用 Docker | 本项目使用该类工作环境；按目标发行版检查 Docker 集成 |
| macOS Intel | Docker Desktop + Bash | 提供配置方法；未在实体 Mac 验证 |
| macOS Apple Silicon | Docker Desktop，必要时 amd64 模拟 | 未验证；不能承诺原生 ARM 支持或与 x86 性能一致 |
| Linux HPC | Apptainer/Singularity-compatible runtime | 提供候选命令；本发布未进行集群/SIF 集成测试 |
| 原生 Windows Python | 不推荐 | inplace 依赖 `fcntl`，ANTs 和固定 XCP-D 环境也不是本仓库的 Windows 原生目标 |
| 原生 Linux Python | 自行提供完整依赖 | 代码不依赖 Docker API；须通过同一套测试 |

## Linux

安装适用于系统的 [Docker Engine](https://docs.docker.com/engine/install/)，并确保当前用户可以执行 `docker run`。需要对输入具有读权限，对结果目录具有写权限；inplace 还需要对原 DeepPrep 输出根目录和 BOLD 有写权限。

```bash
docker version
bash scripts/docker_adapter.sh --version
bash scripts/docker_adapter.sh test
```

包装脚本在 Linux/WSL 下使用调用者的 UID/GID，避免输出变成 root 所有。需要额外组权限的共享存储可使用完整 Docker 命令添加相应 `--group-add`。原目录暂存建在 BOLD 的父目录，因此只给 BOLD 子目录写权限可能不足。

服务器无桌面不影响运行。包装脚本不分配 TTY，可用于终端或调度作业。远程 Docker context 的 bind mount 使用的是 Docker daemon 所在机器的路径，不能直接访问客户端本地磁盘；见 [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/)。

## Windows / WSL 2

1. 安装 WSL 2 和一个 Linux 发行版，例如 Ubuntu；参照 [Microsoft WSL 安装说明](https://learn.microsoft.com/windows/wsl/install)。
2. 使用 Docker Desktop 的 Linux containers / WSL 2 backend，启用对应发行版的 WSL Integration；参照 [Docker 官方配置](https://docs.docker.com/desktop/features/wsl/)。
3. 打开 **Ubuntu/WSL 终端**，在其中 clone 仓库、设置路径并运行首页命令。

例如 Windows 的 `E:\MRI Data\deepprep` 在 WSL 常见路径为：

```bash
export DEEPPREP_DIR='/mnt/e/MRI Data/deepprep'
export ADAPTER_RESULTS='/mnt/e/MRI Data/adapter-results'
mkdir -p "$ADAPTER_RESULTS"
bash scripts/docker_adapter.sh --version
```

不要把 Bash 命令中的 `\` 续行符直接粘贴到 PowerShell，也不要把 `E:\...` 传给容器中的 Python。`/deepprep`、`/result` 是挂载后容器内路径，与 Windows 盘符不是同一层级。

大量小文件和缓存可放在 WSL Linux 文件系统中；跨 `/mnt/c`、`/mnt/e` 读写会受文件共享性能影响，参照 [Docker WSL 文件系统建议](https://docs.docker.com/desktop/features/wsl/best-practices/)。这不意味着必须搬动已有 MRI 数据；先评估容量和实际 I/O。使用 NAS 时应在本次会话确认挂载可读，盘符在 Windows 可见不代表 Docker/WSL 中同样可见。

Git 的 `.gitattributes` 强制脚本使用 LF。若手工复制后出现 `bash\r`、`$'\r'`，重新 clone 通常比逐行修复更可靠。

## macOS

安装适合 Intel 或 Apple Silicon 的 [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/)。在 Terminal 中调用 `bash scripts/docker_adapter.sh ...`；脚本兼容系统 Bash 3.2 使用的数组语法。将数据目录加入 Docker 可访问的文件共享范围，并给 Docker VM 分配足够内存。

```bash
export DEEPPREP_DIR='/Volumes/MRI/deepprep_output'
export ADAPTER_RESULTS='/Volumes/MRI/adapter-results'
mkdir -p "$ADAPTER_RESULTS"
bash scripts/docker_adapter.sh --version
```

已验收容器运行架构为 `x86_64`。Apple Silicon 上若需要使用同一 x86 环境，可显式指定：

```bash
export ADAPTER_PLATFORM='linux/amd64'
bash scripts/docker_adapter.sh --version
bash scripts/docker_adapter.sh test
```

Docker 可使用 amd64 模拟运行 Intel 容器，但存在性能和兼容性限制，详见 [Docker 已知问题](https://docs.docker.com/desktop/troubleshoot-and-support/troubleshoot/known-issues/)。这里没有宣称固定镜像提供原生 arm64 构建。若测试失败或镜像不能拉取，使用经过验证的 x86_64 Linux/WSL 主机，不要通过取消版本/质量检查绕过失败。

`--jobs` 与 Docker VM 分配到的内存共同决定实际容量；Mac 的物理内存总量不等于容器可用内存。本发布未在 Intel/Apple Silicon Mac 上验证结果。

## Linux 集群：Apptainer

以下是 **待在目标集群验证** 的用法。先遵守集群的容器和计算节点政策；不要在登录节点运行完整逆场。Docker 容器可以转换为 SIF，bind mount 与环境隔离规则参见 [Apptainer OCI 支持](https://apptainer.org/docs/user/main/docker_and_oci.html)、[挂载说明](https://apptainer.org/docs/user/main/bind_paths_and_mounts.html)。

```bash
apptainer pull xcpd-26.2.0.sif \
  docker://pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca

apptainer exec --cleanenv \
  --bind "$PWD:/adapter:ro" \
  xcpd-26.2.0.sif \
  python /adapter/deepprep_to_xcpd.py --version
```

如果镜像导出后的 `python` 不在 PATH，先检查 `apptainer exec ... env` 和镜像内的实际安装路径，不要假定宿主机 Python 与镜像内 Python 相同。若命令环境正确，先跑合成测试，再运行数据：

```bash
apptainer exec --cleanenv \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --env DISABLE_SQLALCHEMY_CEXT_RUNTIME=1 \
  --env ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=2 \
  --env OMP_NUM_THREADS=2 \
  --env OPENBLAS_NUM_THREADS=1 \
  --env MKL_NUM_THREADS=1 \
  --bind "$PWD:/adapter:ro" \
  xcpd-26.2.0.sif \
  python -m unittest discover -s /adapter/tests -v
```

对应 plan 示例，需先设置首页的 `DEEPPREP_DIR` 和 `ADAPTER_RESULTS`：

```bash
apptainer exec --cleanenv \
  --bind "$PWD:/adapter:ro" \
  --bind "$DEEPPREP_DIR:/deepprep:ro" \
  --bind "$ADAPTER_RESULTS:/result" \
  xcpd-26.2.0.sif \
  python /adapter/deepprep_to_xcpd.py plan \
  --deepprep-dir /deepprep/BOLD --subjects 001 --task rest \
  --space MNI152NLin6Asym --resolution 2 --manifest /result/manifest.json
```

转换时保留相同挂载，并使用测试命令中的线程环境设置；inplace 需将 `/deepprep:ro` 改为 `/deepprep`。调度器分配的总 CPU/内存需要覆盖所有 worker。Apptainer 原生 Linux 运行不会自动提供跨 CPU 架构模拟，ARM 集群不可假定能运行此已验证 x86 环境。

## 不使用容器

本仓库没有把 XCP-D、ANTs 和科学库打包成一个可跨操作系统 `pip install` 的环境。仅 `pip install numpy nibabel` 不够。

必须提供：Python（代码使用 Python 3.9+ 的语法/API，实际验证环境为 Python 3.12）、NumPy、SciPy、pandas、nibabel、PyBIDS、**XCP-D 26.2.0**，以及 PATH 中的 `antsApplyTransforms`。依赖本身的 Python 支持范围还须满足；代码最低语法版本不构成环境支持承诺。inplace 需要 Unix `fcntl`。

```bash
python deepprep_to_xcpd.py --version
python -c 'import importlib.metadata as m; print(m.version("xcp_d"))'
antsApplyTransforms --version
python -m unittest discover -s tests -v
```

通过后使用宿主机真实路径重新 plan。容器内的 `/deepprep`、`/result` 路径不能直接在本地 Python 下使用。请记录安装环境，参照发布测试记录中的版本，而不是擅自修改精确的 XCP-D 版本检查。

## Docker 包装脚本的环境变量

这些变量只改变启动方式，不改变适配器数值质量门槛。

| 变量 | 默认/用途 |
|---|---|
| `DEEPPREP_DIR` | DeepPrep 输出根目录；挂载 `/deepprep` |
| `ADAPTER_RESULTS` | 已存在的结果目录；plan/convert/validate/rollback 时需设置 |
| `BIDS_DIR` | 可选，挂载参与者映射来源到 `/bids:ro` |
| `EXTRA_WORK_DIR` | 可选，挂载额外工作来源到 `/extra_work:ro` |
| `ADAPTER_IMAGE` | 默认固定 digest；离线可指向已核对 image ID 的本地标签 |
| `ADAPTER_PLATFORM` | 默认不传 Docker platform；需要 amd64 模拟时设置 `linux/amd64` |
| `ADAPTER_ITK_THREADS` | 默认 2 |
| `ADAPTER_OMP_THREADS` | 默认 2 |
| `ADAPTER_BLAS_THREADS` | 默认 1，同时设置 OpenBLAS/MKL 线程环境 |

例如调整线程数后运行同一转换命令：

```bash
export ADAPTER_ITK_THREADS=1
export ADAPTER_OMP_THREADS=1
export ADAPTER_BLAS_THREADS=1
```

数值结果仍须通过相同检查。覆盖 `ADAPTER_IMAGE` 时请保持 XCP-D 26.2.0 和全部依赖，代码会拒绝其他 XCP-D 版本。脚本不读取 `.env`，不自动下载图谱，也不提供运行 XCP-D 本身的入口。
