# 发布验证

[返回首页](../README.md)

当前为 2.3.0。本次 31 项选择性测试通过；真实 ANTs/XCP-D 容器集成未重跑。详见 [2.3.0 验证记录](compatibility_2.3.0.md) 与 [日志](compatibility_tests_2.3.0.txt)。

以下覆盖范围、summary.json 和 tests.txt 保存 **2.2.0 历史容器验收**，不能作为 2.3.0 全套测试结果。

## 覆盖范围

- 26 项原适配器测试：物理坐标、非等距/翻转 affine、ANTs 拉回方向、数值逆、BOLD 优先、工作目录回退、session/run/task 筛选、confounds 时间对应。
- 实际 XCP-D 读取，包含无 session 与有 session 的 run。
- 双进程 CLI 转换与串行结果的影像、affine、数值报告一致性。
- 独立 copy/symlink、两被试单事务 inplace、回滚、发布冲突和外部编辑保护。
- worker 失败时两种布局均不发布，非法 jobs 提前拒绝，模拟 KeyboardInterrupt 的诊断保留。
- 5 项发布新增包装脚本测试：含空格/特殊字符路径、只读/可写切换、额外挂载、离线镜像/平台参数、缺失目录处理。

共 **31 项**。实际运行结果及环境见 [summary.json](summary.json)，完整合成测试日志见 [tests.txt](tests.txt)。这些测试不需要研究影像。

```bash
bash scripts/docker_adapter.sh test
```

## 边界

测试使用固定 XCP-D 26.2.0 容器中的真实 ANTs/XCP-D 运算，运行架构为 x86_64。包装脚本在本发布的 Linux/WSL Bash 环境中执行；macOS、ARM 模拟、Apptainer 说明未在这些平台实际验收。

KeyboardInterrupt 测试为异常注入，不等于系统断电/SIGKILL 恢复实测。测试通过不保证任意新数据集配准质量，不能替代视觉 QC；本发布没有重新运行任何真实队列的完整 DeepPrep 或 XCP-D 后处理。

仓库 SHA256SUMS.json 对发布源文件、文档与验证记录做校验；不包含自身，也不包含 Git 元数据。目标设备应先跑测试，再用自己的一个被试检查完整转换。
