# 完整使用指南

[返回首页](../README.md) · [平台设置](platforms.md) · [跨设备迁移](migration.md)

本工具优先从已完成的 DeepPrep 最终输出中，整理出 XCP-D 可以读取的 **NIfTI 体积数据集**。它复用预处理 BOLD 和 confounds，转换原有配准的格式，补建脑掩膜、逆变换和三维参考图；不重跑 BOLD 预处理，不做回归、滤波、despike 或删帧。最终输出缺少所需来源时，才利用保留的工作文件补充。

代码入口：`deepprep_to_xcpd.py`；坐标算法：`transforms.py`；原目录事务发布：`inplace.py`；测试：`tests/`。新增的 `compatibility.py` 处理读取 API 和来源记录；这些文件组成独立工具；不依赖任何项目专用脚本。

**支持两种输出布局（当前版本 2.3.0）：**

| 选项 | 行为 | 适用情况 |
|---|---|---|
| `--layout separate`（默认） | 创建新的 XCP-D 输入目录；可选择 `--mode copy` 或 `symlink` | 保持原 DeepPrep 输出不变，或需要 copy 模式下可独立搬移的数据集 |
| `--layout inplace` | 在原 DeepPrep `BOLD` 内补齐；已有 BOLD/T1/confounds 直接复用 | 节省大体积影像副本，希望 XCP-D 直接读取原 `BOLD` 目录 |

原目录模式也会先生成并验证临时适配视图；已有大文件在这个临时视图中使用链接，不复制 BOLD。验证通过后仅发布缺失文件及允许的兼容修正。不会重新执行 DeepPrep 或 XCP-D 的影像后处理。

## 1. 支持范围

- 输入按格式判断：**DeepPrep SynthMorph joint，物理 RAS 毫米位移场**；不限制软件版本。
- 目标为 **XCP-D NIfTI、fMRIPrep 兼容读取器**；按已安装读取接口和实际读取结果检查，不设置版本白名单。DeepPrep 24.1.2 / XCP-D 26.2.0 是历史完整验证环境。
- 不限定被试数量、任务名称、帧数或 TR；从选择的文件和元数据读取。要求单回波，BOLD 帧数与 confounds 行数一致，NIfTI/JSON 的 TR 一致。当前实测项目使用 MNI152NLin6Asym、2 mm、rest。
- 路径、被试编号、task、BOLD 分辨率均可配置；支持无 session 的 `sub-X/func` 和含 session 的 `sub-X/ses-Y/func`，支持同一被试多个 run。
- 每个被试使用一套共同的、与所有选中 BOLD 对应的 T1 配准。**不同 session 各有独立 T1 配准时，不要混为一套**；需拆分输入或扩展按 session 绑定结构像的实现。
- CLI 接受 MNI152NLin2009cAsym，但验证项目未实测该模板。它仍需相同源形变定义、完整的配准输入/输出和全部质量门槛；不能仅修改 `space` 来改变真实模板。
- 版本号不会阻止运行；数据仍须满足上述位移场约定。不能把 ANTs/FSL 形变或绝对坐标场直接当作 RAS 毫米位移。程序持续检查 T1 重现、位移场几何、求逆质量和实际读取结果。

`--input-type fmriprep` 只是在 XCP-D 中选择兼容的文件读取方式。输出的 `GeneratedBy` 如实写 DeepPrep 和本适配器，不伪称数据由 fMRIPrep 产生。新 profile 为 `deepprep-synthmorph-ras-mm`；旧 `deepprep-24.1.2-synthmorph-ras-mm` 保留为兼容别名。它们描述同一数据约定，不鉴定或限制源软件版本。GeneratedBy 从源 dataset_description.json 保留；未知版本不再写成 24.1.2。

该工具不承诺补齐 XCP-D 的所有可选分支：不会凭空生成 fsLR-91k CIFTI、皮层表面、髓鞘图、个体 HCP 分区或不同策略的 CompCor 成分。已验证的是 **36P、NIfTI、LINC QC、体积分区、ALFF/ReHo/FC** 输入需求。其他去噪策略需要另外核对它们所需的 confounds 列和元数据。

## 2. 流程与职责

1. `plan` 优先从 **DeepPrep 最终输出的 `BOLD` 数据集**选择已发布文件，生成明确的来源清单；仅缺少所需文件时才搜索保留的工作目录。
2. `convert` 按清单读取源文件，检查时间维度与混杂变量，转换 RAS 位移场、计算数值逆、补齐掩膜和三维参考图，然后验证 XCP-D 读取。
3. 使用 `--layout separate` 创建新输入，或使用 `--layout inplace` 补齐原 `BOLD`。两者都不执行 XCP-D 后处理。
4. 最后单独运行 XCP-D；删初始帧、despike、回归、滤波、平滑及图谱由 XCP-D 命令设置。

DeepPrep 的 `WorkDir` 是上游工作目录，可作为缺失来源的补充；下文 XCP-D 的 `/work` 是下游运行缓存。二者用途不同。原始 BIDS、DeepPrep `BOLD`、适配后的 XCP-D 输入和 XCP-D 后处理输出也不能互相替代。

本工具依据已核实的 DeepPrep 24.1.2 实现：SynthMorph 保存模板格点上的 RAS 毫米位移场；该流程未保存原始逆场；标准空间 boldref 使用第一个 BOLD 帧。适配器复用最终 BOLD，仅补齐读取所需文件，不重新重采样 BOLD。

不同项目的被试数、TR、帧数及分析参数均由实际输入和研究方案确定；工具不将某一队列的参数作为默认数据属性。

## 3. 到底需要保留哪些源文件

推荐将 `--deepprep-dir` 指向 `deepprep_output/BOLD`，也可指向其父级 DeepPrep 输出根目录。**只要必需源文件已齐全，运行转换就不要求保留 WorkDir。** 目录例子：

```text
deepprep_output/
├── BOLD/sub-001/anat/...
├── BOLD/sub-001/ses-01/func/...
├── WorkDir/...                 # 可选，仅用于恢复缺失来源
└── Recon/...
```

文件选择规则：

- 同一被试、task、space、resolution 在 `BOLD` 中已有最终 BOLD 时，仅选择这些已发布的 run。不会因搜索工作文件而把 WorkDir 中额外的 run、重扫或试验结果自动加入。
- 对每个选中 run 的附属文件及被试结构像，先查 `BOLD`；该项缺失时才查同级 `WorkDir` 和显式 `--search-root`。各补充目录属于同一后备优先级；参数顺序不是可信度排序。
- 如果该被试的选定条件下完全没有已发布 BOLD，才尝试在补充目录发现同名完整 BOLD。此时清单需要人工核对运行完成状态和采集归属；有文件不代表 DeepPrep 成功完成。
- 若仅某个预期 run 未发布，而其他 run 已发布，工具不会自动补入该 run。需要确认该 run 完整后，显式编辑 manifest 中的 `runs`。
- 相同优先级的多个候选，字节相同才去重，内容不一致则停止。文件名无法证明来自同一次配准，后续还须通过变换与时间维度检查。
- 自动索引不进入隐藏目录、`code` 或目录符号链接；文件符号链接可读取，manifest 记录解析后的实际路径。因此容器还须挂载这些实际目标路径。

| 清单字段 | 文件含义 | 常见 DeepPrep 24.1.2 文件名 |
|---|---|---|
| `native_t1w` | DeepPrep 预处理 T1，通常为 FreeSurfer conformed 空间 | `sub-001_desc-preproc_T1w.nii.gz` |
| `native_mask` | 与上述 T1 完全同网格的二值脑掩膜 | `sub-001_desc-brain_mask.nii.gz` |
| `registration_moving` | 原 SynthMorph 注册时实际输入的 T1 | `sub-001_space-T1w_res-2mm_desc-skull_T1w.nii.gz` |
| `registration_warped` | 上述 T1 经过原形变后保存的结果，用作验证 | `sub-001_space-MNI152NLin6Asym_res-02_desc-skull_T1w.nii.gz` |
| `forward_ras` | 与上述注册匹配的原始 RAS 位移场 | `sub-001_from-T1w_to-MNI152NLin6Asym_desc-joint_trans.nii.gz` |
| `bold` | 最终标准空间 4D BOLD | `..._space-MNI152NLin6Asym_res-2_desc-preproc_bold.nii.gz` |
| `bold_json` | BOLD 元数据，必须有正确 TR | 与 BOLD 同名 `.json` |
| `confounds` / `confounds_json` | 同一 run 的完整混杂变量和元数据 | `..._desc-confounds_timeseries.tsv/json` |
| `t1w_mask` | 同一 run 的 T1w 空间功能脑掩膜 | `..._space-T1w_desc-brain_mask.nii.gz` |
| `boldref` | 对应标准空间参考图，可为 3D 或 4D 单帧 | `..._space-MNI152NLin6Asym_res-2_boldref.nii.gz` |

这里的 native/T1w 是 **DeepPrep 实际使用的物理坐标系**，不能换成原始 BIDS T1 或 scanner 空间文件后仍套用同一个形变。`registration_warped` 是该被试的已配准 T1，**不是**标准模板 T1；用错后无法通过重现检查。

如果工作文件叫 `mask.nii.gz`、`combined.tsv` 等通用名称，自动搜索不会猜它属于哪个 run。依据该节点的 `.command.sh`、日志或 provenance，确定归属后，在 manifest 中填写准确路径。清单中的文件路径与可选 `source_dataset` 可为容器内绝对路径，或相对 manifest 所在目录的路径。新项目应运行 `plan` 或填写仓库中的通用模板。`source_dataset` 表示对应的 BOLD 数据集根目录，用于原目录模式的目标核对。

自动生成 manifest 的前提是关键文件能按 BIDS 文件名识别；如果不能，可以从 [examples/manifest_template.json](../examples/manifest_template.json) 手动填写。遇到同一优先级的多个候选，字节相同的会去重，内容不一致则报错，不按修改时间猜测。

**恢复边界：**

- 只有 `t00000.nii.gz` 等逐帧文件时，本工具不会自动拼出 BOLD；需要先核对帧数、顺序、网格、TR、运行完成状态及对应 confounds，按原版本节点恢复最终 4D BOLD。
- `confounds_part1.tsv` 不能替代最终 confounds。当前 DeepPrep 还会合并其他列；缺失完整运动/组织信号和对应中间文件时，应补跑该节点。
- 缺失 boldref 时，可设 `boldref: null`，按已核实的 24.1.2 约定从最终 BOLD 第 1 帧生成；它不是重新估计的平均参考图。
- 原形变、原配准输入或配准结果丢失时，严格验证不能成立；工具会停止。不能用另一次配准的逆场与旧 BOLD 混搭。
- 缺少真实 TR、空间定义或完整 nuisance 列时不编造 metadata，不补零伪装。

## 4. 变换算法与正确性

原始前向**图像**变换在模板点 `x` 上保存 `d(x)`，实际采样 T1 的坐标为：

```text
F(x) = x + d(x)             x 是模板空间物理 RAS 坐标
warped_T1(x) = native_T1(F(x))
```

因此，文件命名 `from-T1w_to-MNI...` 描述的是图像重采样方向；其内部坐标拉回映射是模板→T1w。不要再把它当作 T1w→模板点坐标变换使用。

ITK/ANTs 使用 LPS 位移向量，所以向量分量按 `[-dx, -dy, dz]` 转换；NIfTI affine 仍按 NIfTI 的 RAS 约定保存。保存形状 `(X,Y,Z,1,3)`、vector intent、毫米单位。不是翻转体素数组，也不是只改后缀为 `.h5`。

逆场求解 `F(x)=y`，在 native T1 网格上保存 `x-y`：先拟合全局 affine 作为预条件，再进行阻尼固定点迭代；少量残差较大的脑内点用小步长追加迭代。这是原前向场的**数值逆**，不冒充 SynthMorph 未保存的原始双向网络输出。

每次转换都执行以下门槛，不根据旧报告跳过：

| 检查 | 当前工程验收门槛 |
|---|---|
| 转换后的场用 ANTs 重现已保存的配准 T1 | 脑内相关 ≥0.9999，相对 RMSE ≤0.001 |
| 模板脑内有限差分 Jacobian | 全部有限且 >0 |
| 模板脑内坐标往返误差 | p99 ≤0.5 mm，最大 ≤2 mm |
| native 脑内坐标求逆残差 | 最大 ≤0.01 mm |
| 两个方向的目标点位于相应场网格支持范围 | 各 ≥99.9% |
| 脑掩膜往返 Dice | ≥0.95 |
| 功能 mask/reference | 二值非空 mask、3D reference，均与最终 BOLD 空间网格一致 |
| 时间与 nuisance | 帧数=TSV 行数，TR 与秒单位 NIfTI 一致，36P 列完整 |

这些阈值是本适配器的工程检查，不是文献规定的普适临床质量标准。有限差分 Jacobian 检查也不构成全局可逆性的数学证明。脑外采用边界延拓，不能拿它当已验证的全头配准；脑内仍允许极少量边界外点，覆盖比例会明确记录。肉眼检查 T1/模板、BOLD/掩膜叠加仍有必要。

## 5. 运行方式：先选一个被试

推荐在现成 XCP-D 容器内执行，已包含 Python、NumPy、SciPy、pandas、nibabel、PyBIDS 和 `antsApplyTransforms`，无需 GPU。当前核实的不可变镜像为：

```text
pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca
```

以下为通用例子，将 `/path/to/...`、`001`、`rest`、模板和分辨率替换为你的项目实际选择。两步必须使用相同的容器内源路径；manifest 保存的是容器可见路径，不是宿主机路径。下面挂载整个 DeepPrep 输出，既能优先使用 BOLD，也能在需要时访问同级 WorkDir。

先创建保存 manifest 和结果的目录；`adapter_results` 可以已存在，但将要创建的 `xcpd-input` 子目录必须不存在：

```bash
mkdir -p /path/to/adapter_results
```

所有 Docker 命令均在宿主机终端执行。

第一步，生成清单，不转换影像：

```bash
docker run --rm \
  -e DISABLE_SQLALCHEMY_CEXT_RUNTIME=1 \
  -v /path/to/deepprep2xcpd:/adapter:ro \
  -v /path/to/deepprep_output:/deepprep:ro \
  -v /path/to/adapter_results:/result \
  --entrypoint python \
  pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca \
  /adapter/deepprep_to_xcpd.py plan \
  --deepprep-dir /deepprep/BOLD \
  --subjects 001 --task rest --space MNI152NLin6Asym --resolution 2 \
  --manifest /result/manifest.json
```

`--deepprep-dir /deepprep` 与上面的 `/deepprep/BOLD` 使用相同的优先顺序。旧的 `/deepprep/WorkDir` 入口为兼容而保留，也会先查同级 BOLD，但不作为推荐用法。

其他位置或非标准名称的工作目录必须显式挂载，例如增加 `-v /path/to/retained_work:/extra_work:ro`，并在 `plan` 添加 `--search-root /extra_work`；`convert` 也须保留该挂载。只有 BOLD 的项目无需创建空的 WorkDir；只有 WorkDir、没有 BOLD 的恢复场景，可使用 WorkDir 入口或手填 manifest，并输出到新的独立目录。

如果需要保留原文件名映射等被试信息，额外挂载原 BIDS 目录，并传 `--participants-tsv /bids/participants.tsv`；独立输出只保留本次选中的行和全部原有列；原目录模式合并并保留原表全部行列。未提供时，独立输出生成最小 participant_id 表，原目录模式保留已有信息；不自动查找原始 BIDS 中的个人信息。转换时也须保持 `/bids` 挂载。

第二步，检查清单中的路径和归属后执行转换：

```bash
docker run --rm \
  -e DISABLE_SQLALCHEMY_CEXT_RUNTIME=1 \
  -e ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=2 \
  -v /path/to/deepprep2xcpd:/adapter:ro \
  -v /path/to/deepprep_output:/deepprep:ro \
  -v /path/to/adapter_results:/result \
  --entrypoint python \
  pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca \
  /adapter/deepprep_to_xcpd.py convert \
  --manifest /result/manifest.json --output /result/xcpd-input \
  --layout separate --mode copy
```

`copy` 为默认值，数据集能独立移动和挂载；BOLD 复制本身不改变像素、时间点或 header。磁盘占用主要是 BOLD 的副本。`symlink` 节省空间，但必须保持原相对目录结构并挂载链接所需的所有路径，不能只挂载适配目录。

转换默认按清单串行执行（`--jobs 1`）；可用 `--jobs N` 开启逐被试并行，详见 5.2。每名被试的多个 run 仍在同一个进程内处理。在 `separate` 模式下，所有源数据只读；输出必须是不存在的新目录。中途失败只留下 `.输出名.building-*` 诊断目录和 `FAILED.json`，不会发布成最终输入。修正原因后换一个新的/不存在的输出位置重跑，不复用旧 passed 标记。

如需多人转换，必须显式在 `plan --subjects 001 002 ...` 中列出；脚本没有默认全队列行为。

### 5.1 选择直接补齐原 DeepPrep 输出

`plan` 命令不变。转换时把 DeepPrep 挂载改为可写，并指定 `--layout inplace`。`--output` 可填写原 DeepPrep 根目录（自动定位其中的 `BOLD`），也可直接填写 `BOLD` 数据集目录：

```bash
docker run --rm \
  -e DISABLE_SQLALCHEMY_CEXT_RUNTIME=1 \
  -e ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=2 \
  -v /path/to/deepprep2xcpd:/adapter:ro \
  -v /path/to/deepprep_output:/deepprep \
  -v /path/to/adapter_results:/result \
  --entrypoint python \
  pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca \
  /adapter/deepprep_to_xcpd.py convert \
  --manifest /result/manifest.json \
  --output /deepprep/BOLD --layout inplace
```

`--mode` 仍默认为 `copy`，原目录模式自动复用已有文件；不要传 `--mode symlink`。从 WorkDir 恢复的缺失文件会实际复制到 BOLD，避免新增对工作目录的链接依赖。已经存在的原始链接不被强行改成副本。

原目录模式的处理规则：

- 已有且相同的 BOLD、T1、掩膜及 confounds 不改写；未选被试、其他任务、空间和 run 保留。
- 只允许把值和空间网格相同的 4D 单帧 boldref 改成 3D，或兼容调整同值 3D reference 的 header；修改前备份。遇到不同体素值的同名 reference 会停止。
- 原 RAS `desc-joint_trans` 保留，另加 ITK LPS `mode-image_xfm` 和逆场；不覆盖原形变。
- 同名 BOLD/confounds/掩膜/变换存在内容冲突时停止，不静默替换为另一份。
- 保留原 `dataset_description.json` 的名称、软件和引用字段，追加适配器记录；保留参与者表全部原行和列。提供的映射只填空值，遇到已有非空值冲突时停止。
- 整个操作持有数据集锁；同一 BOLD 目录不能同时运行两个原目录适配进程。也应避免 DeepPrep 或 XCP-D 在此期间读取或写入这批正在更新的输入。
- 先验证临时视图，再检查所有目标冲突并建立备份，然后逐文件原子发布，最后对**合并后的原 BOLD 目录**再调用 XCP-D 读取器。这里不是整个目录的一次原子切换。
- 普通异常和 Ctrl-C 会回滚已经写入的文件；断电、SIGKILL 或原生库段错误后，应使用下方 rollback 恢复未完成事务。后续发布会拒绝跳过未完成事务。

每次操作的历史保存在：

```text
BOLD/code/adapter/inplace-runs/<transaction-id>/
├── journal.json               # 状态、复用清单、修改前后 SHA256
├── backups/*.backup           # 原文件字节备份；使用不被 BIDS 识别的名字
├── records/                   # 本次 manifest、参数过滤器、变换检查、源校验值
└── reader_validation.json     # 合并目录的最终读取验证
```

`journal.json` 的状态为 `COMMITTED` 才表示补齐并验证成功。根部 `BOLD/code/adapter/` 保存最近一次适配检查，历史被试/任务的记录保存在各事务的 `records/`。manifest 和 source_checksums 记录转换前来源；原路径上的 reference 被兼容修改后，原字节仍可由 journal 的备份映射和 SHA256 追溯。

回滚一个事务（将 `TRANSACTION_ID` 替换为实际事务号，不要使用尖括号占位符）：

```bash
docker run --rm \
  -v /path/to/deepprep2xcpd:/adapter:ro \
  -v /path/to/deepprep_output:/deepprep \
  --entrypoint python \
  pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca \
  /adapter/deepprep_to_xcpd.py rollback \
  --input /deepprep/BOLD --transaction TRANSACTION_ID
```

回滚按 SHA256 检查目标文件：恢复被替换的原文件、删除本事务新增文件，保留备份和记录；不会覆盖发布后被其他程序修改的内容。多次补齐应从最新事务依次向前回滚。回滚后可能保留空目录和适配器日志。重复转换会重新做输入与配准检查，复用一致文件，不把以前的 passed 当作这次成功。

准备完毕后，XCP-D 的输入可直接设为 `/input`，把宿主机 `deepprep_output/BOLD` 挂载到 `/input:ro`，并传：

```bash
--bids-filter-file /input/code/adapter/input_filter.json
```

`validate` 未指定 `--subjects` 时验证最近一次 manifest 选中的被试，不会自动要求未补齐的其他被试也通过。

处理原目录的其他任务时，使用对应事务中保存的过滤器；当前根目录过滤器只描述最近一次选中的 task/space。其他 session 的 T1 若使用不同的配准，仍不在本工具支持范围内。

### 5.2 可选的逐被试并行转换

`convert --jobs N` 同时准备最多 N 名被试，支持 `separate`（copy/symlink）和 `inplace`。默认 `1` 保持串行，允许范围为 `1–16`；实际进程上限为 `min(N, 清单被试数)`。例如单被试 manifest 即使指定 `--jobs 8`，也只使用串行路径。

此功能整合自另一项目的 `2.1.1+smhc.parallel1`：并行范围仅为被试各自的变换、掩膜、参考图和暂存文件。数值算法、质量阈值和 BOLD 优先的来源选择均沿用当前工具。**它不控制 XCP-D 的 `--nprocs`，也不启动 XCP-D 后处理。**

使用方法：先按第 5 节生成包含多名被试的清单，例如 `plan --subjects 001 002`，然后在转换命令添加 `--jobs 2`。以下为原目录补齐的完整转换示例：

```bash
docker run --rm \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e DISABLE_SQLALCHEMY_CEXT_RUNTIME=1 \
  -e ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=2 \
  -e OMP_NUM_THREADS=2 \
  -e OPENBLAS_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 \
  -v /path/to/deepprep2xcpd:/adapter:ro \
  -v /path/to/deepprep_output:/deepprep \
  -v /path/to/adapter_results:/result \
  --entrypoint python \
  pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca \
  /adapter/deepprep_to_xcpd.py convert \
  --manifest /result/manifest.json \
  --output /deepprep/BOLD --layout inplace --jobs 2
```

创建独立目录时，将源挂载改成 `/deepprep:ro`，转换参数改为 `--output /result/xcpd-input --layout separate --mode copy --jobs 2`。若 manifest 使用额外的来源目录或参与者表，仍须保留对应挂载。本地 Python 用法同样只需在 `convert` 增加 `--jobs N`。

执行顺序与失败处理：

1. 父进程检查清单并读取所有来源的 SHA256。原目录模式从开始到结束持有同一把数据集锁。
2. 使用 Python `spawn` 子进程并行处理被试；各被试写入独立暂存子目录。任务提交数量不超过实际并发数。
3. 全部被试成功后，父进程按清单顺序汇总报告，串行调用 XCP-D 读取器并复核全部源校验值。
4. 独立模式统一发布新目录；原目录模式使用一次事务发布全部选中被试，并再检查合并后的数据集。子进程不修改原数据集。
5. 某名被试失败时停止补发新任务，取消尚未开始的任务，并等待正在执行的子进程退出。此阶段尚未发布科学文件；保留暂存目录和带被试/阶段信息的 `FAILED.json`。等待结束前命令可能不会立即返回。发布阶段发生的异常仍按既有事务机制回滚。

`PASS sub-...` 表示该被试暂存转换通过；只有最终的数据集发布成功才可将整批输入交给 XCP-D。本工具没有跨次转换的断点续跑；修复原因后重跑仍会重新计算和验证。

资源与记录：

- 多进程会增加峰值内存和磁盘读写；`--jobs` 不代表 CPU 总线程数。ITK、OpenMP、BLAS 各自的线程数也会影响资源占用，示例分别用环境变量限制。
- 工具不会自动检测可用内存或根据内存降低并发。先用少量被试测量实际内存，再提高并发；引用项目的 8/12 并发是其机器配置，不作为其他项目默认值。不同数据集同时运行时也要合计资源占用。
- SHA256、XCP-D 读取验证和最终发布仍是串行阶段，增加 `--jobs` 不会加速所有步骤。日志会标明 `HASHING_SOURCES`、`STAGING_SUBJECTS`、`VALIDATING_READER`、`RECHECKING_SOURCES`、`PUBLISHING`，便于区别计算与校验耗时。
- `code/adapter/execution.json` 和 `validation.json` 的 `execution` 字段记录请求/实际并发上限、启动方式和线程环境变量。每个被试报告记录工作进程 PID 与耗时；线程环境变量为 `null` 表示未设置，不能据此推断底层实际线程数。原目录事务的 `records/` 保存相同执行记录。

## 6. 输出及直接接入 XCP-D

```text
xcpd-input/
├── dataset_description.json
├── participants.tsv
├── code/adapter/
│   ├── source_manifest.json
│   ├── source_checksums.json
│   ├── execution.json
│   ├── input_filter.json
│   ├── validation.json
│   └── sub-001.json
└── sub-001/
    ├── anat/
    │   ├── sub-001_desc-preproc_T1w.nii.gz
    │   ├── sub-001_desc-brain_mask.nii.gz
    │   ├── sub-001_space-MNI152NLin6Asym_desc-brain_mask.nii.gz
    │   ├── sub-001_from-T1w_to-MNI152NLin6Asym_mode-image_xfm.nii.gz
    │   └── sub-001_from-MNI152NLin6Asym_to-T1w_mode-image_xfm.nii.gz
    └── ses-01/func/...
```

生成的 JSON 记录来源、插值及算法；source_checksums 保存源文件 SHA256，转换前后再次比对以发现处理期间的源文件变化。metadata 中的来源路径是记录，不是 copy 模式运行时的依赖。对外分享时可按需要处理来源路径中的本机信息。

转换完成会调用本镜像的 `collect_data` 和 `collect_run_data`，检查全部选中 run 是否被 XCP-D 正确读取。此处的 PyBIDS `validate=False` 是兼容读取设置，**不等同于通过完整 BIDS Validator**。

`input_filter.json` 将 xfm 的 extension 限定为 `.nii.gz`。这是验证项目实际遇到过的问题：未限定时，同名 JSON 来源说明可能被错误选为变换输入；必须把这个 filter 传给 XCP-D。

独立再检查读取，不运行任何后处理。下例针对 `separate + copy`；inplace 模式应挂载 DeepPrep 目录并改用 `--input /deepprep/BOLD`，symlink 模式需额外挂载所有链接目标：

```bash
docker run --rm \
  -e DISABLE_SQLALCHEMY_CEXT_RUNTIME=1 \
  -v /path/to/deepprep2xcpd:/adapter:ro \
  -v /path/to/adapter_results:/result \
  --entrypoint python \
  pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca \
  /adapter/deepprep_to_xcpd.py validate \
  --input /result/xcpd-input --subjects 001 --report /result/reader_validation.json
```

以下是 **单被试 XCP-D 接入示例**，采用去前 5 帧、despike、不按 FD 删帧、36P、0.01–0.08 Hz 和 6 mm；这些是分析策略示例，不是适配器的要求，也不是所有项目的默认推荐。`--task-id` 须与 manifest 的 task 一致，被试须已完成适配。

例子使用内置 `Glasser` 演示命令格式；它不代表已经加载自备 BN246/AAL3/Glasser360。自定义图谱需另外完成空间核对和 XCP-D atlas 数据集配置。新项目应根据研究方案单独确定这些设置。

```bash
mkdir -p /path/to/xcpd_output /path/to/xcpd_work
```

后处理输出和缓存均使用独立目录，不写回适配输入：

```bash
docker run --rm \
  -e DISABLE_SQLALCHEMY_CEXT_RUNTIME=1 \
  -e ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=2 \
  -v /path/to/adapter_results/xcpd-input:/input:ro \
  -v /path/to/xcpd_output:/output \
  -v /path/to/xcpd_work:/work \
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

本示例尚未在本发布执行，`validate` 成功不等于整个 XCP-D 后处理通过。XCP-D 26.2.0 的此 NIfTI 分支不要求 FreeSurfer license；若其他分支需要，另行只读挂载并传 `--fs-license-file`。首次运行还可能需要访问 TemplateFlow 下载未缓存模板/资源。

`output-type censored` 是 LINC 的输出选择；FD=0 时没有按头动额外删除的帧；这个后处理示例另去除前 5 帧。适配工具本身不删帧。不是“先删帧再用插值补回”。6 mm 沿用 XCP-D 的平滑输出分支；FC/ReHo 的计算仍使用流程中未平滑数据。

本工具不提供 BN246、AAL3、Glasser360 的图谱文件或空间变换。自定义图谱应由用户确认版本、许可、标签及模板空间，再按 XCP-D 的 atlas 数据集要求配置。内置 Glasser 示例不会加载这些自定义图谱。

### 6.1 不使用 Docker

Python 代码不调用 Docker；Docker 示例仅提供已经核实的运行环境。本地环境需安装 Python（支持 `str.removeprefix` / `Path.is_relative_to`）、NumPy、SciPy、pandas、nibabel、PyBIDS、XCP-D，且 `antsApplyTransforms` 在 PATH 中。程序按实际读取 API 检查依赖，兼容带有或不带 session 参数的 collect_data 接口。版本号不同不会拒绝运行；缺依赖、接口不匹配或数据检查失败时会给出具体原因。

在具备上述依赖的本地环境中：

```bash
python /path/to/deepprep2xcpd/deepprep_to_xcpd.py plan \
  --deepprep-dir /path/to/deepprep_output/BOLD \
  --subjects 001 --task rest --space MNI152NLin6Asym --resolution 2 \
  --manifest /path/to/adapter_results/manifest_local.json

python /path/to/deepprep2xcpd/deepprep_to_xcpd.py convert \
  --manifest /path/to/adapter_results/manifest_local.json \
  --output /path/to/adapter_results/xcpd-input --layout separate --mode copy
```

原目录补齐则将第二条命令的输出改成 `/path/to/deepprep_output/BOLD` 并指定 `--layout inplace`。本地运行时要在本地重新生成 manifest，不能直接沿用含 `/deepprep`、`/result` 容器路径的清单。本地原目录事务锁使用 Linux `fcntl`；Windows 用户可在 WSL/Linux 环境执行。

## 7. 验证与排错

运行小型测试，不触及研究数据：

```bash
docker run --rm --network none \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e DISABLE_SQLALCHEMY_CEXT_RUNTIME=1 \
  -e ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=2 \
  -e OPENBLAS_NUM_THREADS=1 \
  -e OMP_NUM_THREADS=2 \
  -e MKL_NUM_THREADS=1 \
  -v /path/to/deepprep2xcpd:/adapter:ro \
  --entrypoint python \
  pennlinc/xcp_d@sha256:a919b121d1da8e090bfb3594f49ffeb5ddf2ab491327b04a7b8a1f304b64e7ca \
  -m unittest discover -s /adapter/tests -v
```

测试用翻转且非等距的合成网格检验毫米/体素区别、RAS/LPS 符号、ANTs 图像拉回方向及非单位 affine 逆变换，并检查工作目录同名冲突、session/run 匹配和 task 边界。另验证 BOLD/输出根目录/WorkDir 三种入口的优先级一致、补结构像时不混入额外 run、从后备目录恢复参考图及缺失目录提示。并行回归测试运行真实 `spawn` 双进程，比较串/并行影像与数值报告，并检查 worker 失败不发布、一次事务覆盖两名被试以及回滚。原目录回归测试另外覆盖完整转换与实际 XCP-D 读取、保留其他任务/被试、重复执行、逐层回滚、发布冲突、验证失败恢复、外部编辑保护、符号链接目录和并发锁。

| 失败位置 | 常见原因与处理 |
|---|---|
| Missing/Ambiguous 文件 | 工作节点未保留完整文件、不同 run 重名或多次配准；依据日志填写明确 manifest，不随机取第一项 |
| forward convention/source mismatch | 场的类型/方向/单位错误，或移动 T1、配准后 T1 不属于同一次注册；核对来源，不降低门槛掩盖问题 |
| Jacobian/inverse quality gate | 场局部不可逆、边界支持不足或迭代未收敛；查看 metrics，必要时用同版本注册单独补算并验证与原场对应 |
| confounds 行数/列数不符 | run 选错、上游跳帧或合并未完成；恢复正确表，不随意裁剪或补零 |
| 原始 reference 不是 3D/单帧 | 选错了完整 BOLD；纠正 source path |
| XCP-D No BOLD/session 错误 | 检查文件名中的 sub/ses/task，确保清单每个 run 使用实际 prefix |
| JSON 被当作 transform | 运行 XCP-D 时遗漏了工具生成的 input_filter.json |
| 数据库/模板查询段错误 | 本机镜像中曾出现，示例保留 `DISABLE_SQLALCHEMY_CEXT_RUNTIME=1` 这一兼容设置；它不能保证消除所有数据库段错误，当前项目批量 HTML 汇总仍曾崩溃 |

## 8. 依据与来源

- [XCP-D 26.2.0 最小输入](https://xcp-d.readthedocs.io/en/26.2.0/usage.html#minimal-inputs)
- [XCP-D 26.2.0 参数与过滤器](https://xcp-d.readthedocs.io/en/26.2.0/usage.html)
- 本地 DeepPrep 镜像 `pbfslab/deepprep:24.1.2`，ID `831ed7aad85e53c4b762864930965286ca25f197ff4077a9d071f65c9ad5c470`。
- 镜像内 `/opt/DeepPrep/deepprep/SynthMorph/{mri_synthmorph_joint,bold_synthmorph_joint}.py` 与 `/opt/DeepPrep/deepprep/nextflow/bin/{bold_apply_transform_chain,bold_anat_prepare,bold_confounds_combine}.py`。

工具版本：2.3.0。测试范围见 [validation/README.md](../validation/README.md)，版本差异见 [CHANGELOG.md](../CHANGELOG.md)。

## 版本选择与分辨率

固定镜像命令是可复现示例，不是版本限制。使用其他版本时替换命令中的镜像即可；仓库包装脚本支持 `ADAPTER_IMAGE`。实际运行的依赖版本仍写入 validation.json。旧发布的验证日志保留原环境信息，不表示新版已在全部版本上验收。

适配器的 `--resolution` 选择已有 BOLD 文件，不修改上游配准分辨率，也不重新重采样 BOLD。其逆场输出网格来自实际 native_t1w 的 header，未硬编码 1 mm。

核查的 DeepPrep 实现中，`bold_T1_to_2mm.py` 将配准 moving T1 固定重采样为 2 mm；SynthMorph 的 fixed 模板也固定请求 resolution=2。用户的 `--bold_volume_res` 控制最终标准空间 BOLD/boldref 网格，不控制这两个配准网格。因此改成 1 mm BOLD 输出，不会自动将配准逆场变成 1 mm。原生导出应读取实际图像 header 和物理范围。
