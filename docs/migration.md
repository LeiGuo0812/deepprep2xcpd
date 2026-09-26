# 跨设备迁移与复现

[返回首页](../README.md) · [平台设置](platforms.md)

## 选择迁移对象

| 迁移场景 | 应携带的内容 | 到新设备后的操作 |
|---|---|---|
| 尚未适配，换机器运行 | 代码的固定 commit、DeepPrep BOLD、缺失来源所需 WorkDir/额外目录、可选参与者表 | 用新路径重新 plan，然后 convert |
| `separate --mode copy` 已完成 | 整个 xcpd-input，包含 dataset_description、participants 和 code/adapter | 挂载新路径，运行 validate；无需重做数值逆 |
| `separate --mode symlink` 已完成 | 适配目录和所有链接目标，并保留相对路径关系 | 确认无断链，全部正确挂载，再 validate |
| `inplace` 已完成 | 整个 BOLD，包括 code/adapter/inplace-runs 与备份 | 挂载并 validate；若已有源文件本身是链接，还须带上链接目标 |
| 只搬工作中的临时目录 | 不构成已发布输入 | 不应直接交给 XCP-D；排错后重新转换 |

通常最容易跨设备搬移的是 `separate + copy`。`Sources` 中记录的旧来源路径仅作为 provenance；已完成的 copy 数据集运行 XCP-D 时不需要那些旧文件。相反，真实符号链接必须保持可访问。

inplace 模式不会把源目录原先存在的链接全部改成副本。不能仅因使用了 inplace，就认为 BOLD 已经自包含。

## 统一容器路径

首页包装脚本固定以下映射：

| 宿主机变量 | 容器目录 | 权限 |
|---|---|---|
| 仓库所在目录（自动识别） | `/adapter` | 只读 |
| `DEEPPREP_DIR` | `/deepprep` | 默认只读；inplace/rollback 可写 |
| `ADAPTER_RESULTS` | `/result` | 可写 |
| 可选 `BIDS_DIR` | `/bids` | 只读 |
| 可选 `EXTRA_WORK_DIR` | `/extra_work` | 只读 |

例如两台设备分别使用 `/data/study/deepprep` 和 `/mnt/e/study/deepprep`，只要都映射到 `/deepprep`，容器绝对路径可以一致。但仍应确认实际数据相同；不要用同一个 manifest 指向另一批同名文件。

需要来源恢复或参与者映射时：

```bash
export EXTRA_WORK_DIR='/absolute/path/to/retained_work'
export BIDS_DIR='/absolute/path/to/raw_bids'

bash scripts/docker_adapter.sh plan \
  --deepprep-dir /deepprep/BOLD --subjects 001 002 --task rest \
  --space MNI152NLin6Asym --resolution 2 \
  --search-root /extra_work \
  --participants-tsv /bids/participants.tsv \
  --manifest /result/manifest.json
```

后续 convert 必须保留这些环境变量对应的挂载。设置 `EXTRA_WORK_DIR` 只提供挂载，仍须传 `--search-root` 才纳入搜索。若有多个额外目录，使用完整 Docker 命令逐一 `-v` 挂载并重复 `--search-root`。

manifest 中的相对路径以 **manifest 文件所在目录** 为基准，不是启动命令时的工作目录。自动 plan 会记录解析后的绝对来源路径；符号链接的真实目标可能位于别处，因此仅挂载链接所在目录可能不足。

## 传输已完成的数据集

Linux/macOS 可用 rsync 保留目录结构，路径含空格时加引号：

```bash
rsync -a --progress '/source/adapter-results/xcpd-input/' '/destination/xcpd-input/'
```

上例保留符号链接而不是自动展开链接；copy 输出通常没有这种依赖，symlink 输出需要额外传输目标目录。Windows 文件管理器、网络共享或某些压缩格式对符号链接的处理可能不同，跨平台迁移优先用 copy 输出。

在新设备中，将包含 `xcpd-input` 的目录设置为 `ADAPTER_RESULTS`：

```bash
export ADAPTER_RESULTS='/destination'
bash scripts/docker_adapter.sh validate \
  --input /result/xcpd-input --subjects 001 \
  --report /result/reader_validation_after_transfer.json
```

`validate` 检查读取关系，不会重新验证每个体素，也不会对新旧设备的数据集自动作逐字节比较。传输时应使用具备校验能力的传输工具，或自行比较文件 SHA256。`source_checksums.json` 记录转换前的来源文件，不是适配输出的传输校验清单。

## 离线设备

在有网络的设备上准备同一版本代码和镜像。下面把已拉取的固定镜像打上便于离线加载的本地标签：

```bash
image='pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca'
docker pull "$image"
docker tag "$image" deepprep2xcpd-runtime:26.2.0
docker image inspect "$image" > xcpd-image-metadata.json
docker save -o xcpd-26.2.0.tar deepprep2xcpd-runtime:26.2.0
```

传输仓库、tar 和 metadata 文件；离线设备执行：

```bash
docker load -i xcpd-26.2.0.tar
export ADAPTER_IMAGE='deepprep2xcpd-runtime:26.2.0'
docker image inspect "$ADAPTER_IMAGE"
bash scripts/docker_adapter.sh --version
bash scripts/docker_adapter.sh test
```

`docker save/load` 后本地标签通常比 registry digest 引用更方便；这里通过 `ADAPTER_IMAGE` 显式指定。核对两边 inspect 中的 image ID，并保存传输校验值。不要用碰巧叫相同名字的另一个镜像代替。

适配器数值转换使用已有来源和本地 ANTs；后续 XCP-D 可能还需要 TemplateFlow/atlas 缓存。离线运行 XCP-D 前应另行准备其模板、图谱和软件资源，不能因为适配器离线测试通过，就假定完整后处理不需要网络。

## 固定代码与运行记录

记录所用仓库 commit：

```bash
git rev-parse HEAD
git status --short
```

新设备若要复现同一版本，应 checkout 保存的 commit，而不是默认追随 main 的最新修改。源代码文件校验值保存在仓库 `SHA256SUMS.json`；可用下面的纯 Python 检查：

```bash
python3 - <<'PY'
import hashlib, json
from pathlib import Path
for name, expected in json.loads(Path('SHA256SUMS.json').read_text()).items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected, name
print('All recorded source checksums match')
PY
```

每次正式运行建议保存：manifest、输入来源校验、adapter validation、execution 参数、终端日志、代码 commit、镜像标识。inplace 另外保存 journal 和 backups。`--jobs` 与线程环境也影响资源和执行方式，应一起记录。

本工具不提供跨次转换的断点续跑；已有暂存 PASS 不会让下次自动跳过对应被试。已经完成的独立数据集可以直接迁移并复查，无需再次 convert。
