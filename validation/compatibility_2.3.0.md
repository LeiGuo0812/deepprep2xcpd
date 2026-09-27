# 2.3.0 兼容性更新验证（2026-09-27）

本次移除软件版本硬限制；保留数据格式、几何、变换质量和实际读取检查。新 profile 不带版本，旧 profile 作为别名继续接受。软件来源从源 dataset_description.json 保留，缺失版本不编造。

本次在 Linux/WSL Python 3.10 环境完成 31 项选择性测试（10 项新增，21 项既有），全部通过。依赖仅安装在 /tmp 的隔离目录，没有修改系统 Python 软件包。日志为 compatibility_tests_2.3.0.txt。

覆盖：新旧 collect_data 参数接口、未知必需参数错误、无版本号准入检查、读取器内部异常不被吞掉、来源记录、旧/新 manifest、混合 session、文件搜索优先级、非等距物理坐标逆变换、confounds 长度检查、发布/回滚和 Docker 包装脚本参数。

读取 API 分派测试使用 test double 与真实 PyBIDS 索引；它不等于安装了多个 XCP-D 版本进行验收。包装脚本测试使用假 docker 命令检查参数，也不等于实际启动容器。

本次 Docker Desktop 返回 “Docker Desktop is unable to start”，因此没有重跑真实 ANTs、实际 XCP-D 读取器、完整转换和并行集成测试。没有修改研究影像。2.2.0 的 31 项容器验收日志保持原样，仅为历史证据。

完整仓库套件现为 41 项；项目工具包不含仓库专用的 5 项 launcher 测试，套件为 36 项。在 Docker 恢复后可用仓库的 `bash scripts/docker_adapter.sh test` 运行完整套件；也可在其他具备 ANTs/XCP-D 的环境中直接运行 unittest。基础镜像可通过 ADAPTER_IMAGE 自选。

本次选择性测试命令（在仓库根目录，依赖可用后执行）：

```bash
PYTHONPATH=tests:. python3 -m unittest \
  test_compatibility test_version_policy test_launcher \
  test_adapter.PlanningPriorityTests \
  test_adapter.TransformTests.test_physical_affine_inverse_and_lps_serialization \
  test_adapter.SelectionTests.test_confounds_time_mismatch_rejected \
  test_adapter.SelectionTests.test_conflicting_work_files_rejected \
  test_adapter.SelectionTests.test_subject_path_injection_rejected \
  test_adapter.SelectionTests.test_workdir_fallback_sessions_and_run_boundaries \
  test_inplace.PublicationTests \
  test_parallel.ParallelConversionTests.test_invalid_jobs_rejected_before_lock_or_staging -v
```
