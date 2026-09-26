# 排错与恢复

[返回首页](../README.md) · [完整输入契约](usage.md)

## 先确定失败阶段

| 日志阶段/现象 | 含义与下一步 |
|---|---|
| `HASHING_SOURCES` 很久没有被试进度 | 正在串行读取全部源文件 SHA256，大体积 BOLD 或 NAS 会较慢；增加 jobs 不会加速该阶段 |
| `STAGING_SUBJECTS` | 正在转换被试，PASS 只说明暂存成功，尚未整批发布 |
| `VALIDATING_READER` | 调用 XCP-D 读取器核对所有选中 run |
| `RECHECKING_SOURCES` | 再次计算 SHA256，确认转换期间源文件未变 |
| `PUBLISHING` | 进入新目录发布或原目录事务；inplace 还会验证合并后的数据集 |
| `.输出名.building-*` 与 `FAILED.json` | 保留的失败诊断目录，不是可直接交给 XCP-D 的正式输入 |

失败时先查看 `FAILED.json` 中的 `phase`、`error_type`、`error`，再看该被试的 `transform_metrics.json` 和终端 traceback。不要仅因某些被试有 PASS 就启动整批后处理。

## 常见输入和环境问题

| 问题 | 处理 |
|---|---|
| `Missing source directory` | 确认宿主机挂载存在，并在容器参数中使用 `/deepprep/BOLD` |
| `Missing --search-root` | 检查额外目录路径；设置 EXTRA_WORK_DIR 并加 `--search-root /extra_work` |
| `Missing ...` | 先查已发布 BOLD，再查完整工作来源；generic `mask.nii.gz` 不会被自动猜测归属 |
| `Ambiguous ...` | 多个同级候选内容不同；依据配准/采集日志手工指定 manifest，不随机挑一个 |
| 只找到部分 run | 已有发布 BOLD 时不会自动混入 WorkDir 的额外 run；确认缺失 run 完成状态后手动选取 |
| manifest 已存在 | 使用新的文件名，或人工确认后移走旧清单；工具不会静默覆盖 |
| output 已存在 | separate 必须创建新目录；不能把既有输出当作 resume 缓存 |
| `Confounds rows` / 缺少 36P | 核对同一 run 及上游完整合并结果；不裁剪或补零掩盖问题 |
| NIfTI / JSON TR 不符 | 核对真实 TR 和秒单位头信息；不能仅改标签“通过”检查 |
| `forward convention/source mismatch` | 场的单位/方向/格式错误，或配准输入与结果不属于同一次配准 |
| `Inverse quality gate failed` | 查看往返误差、支持比例和 Dice；不要降低阈值掩盖错误 |
| `antsApplyTransforms not found` / XCP-D 版本错误 | 使用固定镜像，或修复本地完整依赖；普通 Python 不是完整运行环境 |
| `Permission denied` | 检查原目录父级、结果目录及挂载权限；wrapper 在 Linux/WSL 使用当前 UID/GID |
| `Another ... holds the dataset lock` | 已有同目录适配或回滚进程；先确认它的状态，不同时启动第二个写入者 |
| `BrokenProcessPool` / worker 意外退出 | 先查容器/作业退出码和内存限制；减少 jobs。不能单凭该错误判定为 OOM |
| 数据库/SQLite 原生段错误 | 记录软件版本与 traceback。示例中的 DISABLE_SQLALCHEMY_CEXT_RUNTIME=1 是兼容设置，并不保证消除所有崩溃 |
| XCP-D 选错变换 JSON | 使用生成的 input_filter.json，确保 xfm extension 限定为 `.nii.gz` |
| 搬机器后 Missing 文件 | manifest 旧容器路径或实际 symlink 目标没有挂载；重新 plan 或恢复挂载 |

## inplace 回滚

事务保存在：

```text
BOLD/code/adapter/inplace-runs/TRANSACTION_ID/
├── journal.json
├── backups/
├── records/
└── reader_validation.json
```

`COMMITTED` 表示该事务发布和读取验证成功。普通发布异常会自动回滚；断电、SIGKILL 或原生库段错误后，检查 journal 并按需执行显式 rollback。尚未进入发布阶段时可能只有暂存目录，没有事务可回滚。

先设置与转换时相同的 DEEPPREP_DIR 和 ADAPTER_RESULTS，然后把 `TRANSACTION_ID` 替换成真实事务号：

```bash
bash scripts/docker_adapter.sh rollback \
  --input /deepprep/BOLD --transaction TRANSACTION_ID
```

恢复前会检查修改前后的 SHA256；不会覆盖发布后由其他程序修改的文件。若存在多次事务，从最新向前依次回滚。回滚保留审计日志、备份和可能的空目录，不意味着整个树逐目录消失。

并行阶段收到普通失败/中断时，父进程会等待正在执行的 worker 退出；命令不是立即结束。不要在等待期间启动另一次 inplace。硬中断可能留下子进程，重新运行前确认原作业已经停止。

## 如何报告问题

提供工具版本、代码 commit、镜像标识、平台/CPU 架构、执行命令（去除私人路径）、失败阶段、完整错误文本以及相关数值指标。可以使用 GitHub Issues；不要公开患者姓名、真实被试映射、完整私人路径、license 或影像。

`validate` 只检查读取契约；本仓库不会自动决定配准视觉 QC、统计策略或组分析是否合格。
