# Live2D Automation 0.1.0

可执行的 Cubism CLI、17 工具 stdio MCP 和 Codex Skill。针对已有模型，完成检查、参数/结构编辑、透明度眨眼绑定、原生保存重开/导出、动作/表情生成，以及官方 SDK/Core 渲染验证。**这是 Windows 单机 pilot，当前原生编辑适配 Cubism 5.4 Alpha2 / API 1.1.0。**

真实 Editor 与 MCP 完整流程已验证，computer-use=0。官方 API 负责结构/属性编辑；Java Access Bridge 与受限快捷键负责保存重开和导出，会短暂占用焦点。网格顶点雕刻、PSD 制作/导入和桌宠运行时不在 v0.1 范围。结果与边界见 [验收报告](docs/acceptance.md)。

Windows 安装（Python 3.11+，SDK 验证另需 Node 20.19+/22.12+ 与 Edge）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_windows.ps1 -RegisterMcp
& .venv/Scripts/live2d-automation.exe doctor
```

安装只影响本包虚拟环境和独立 Skill/MCP 配置；会备份原 Skill/config。`-SkipRuntime` 可跳过 Node 依赖。重载 MCP 或新建会话后发现 17 个工具；CLI 可立即使用。首次启动 Editor 的 JAB 设置、授权、示例和恢复方式见 [使用指南](docs/user-guide.md) 与 [Skill](skills/live2d-automation/SKILL.md)。

API 写入默认计划模式，需 UID、确认和新鲜快照哈希，跨进程互斥并保留备份/恢复标记。工具不提供任意 shell/raw API，不写伪造 MOC3 或直接修改 CMO3 二进制。Alpha2 适配期限为 2026-10-18，后续版本需重新适配；资源检查与 SDK 验证可独立运行。

开发：`python -m pip install -e '.[dev]'`，然后 `pytest -q`、`ruff check .`、`mypy src`、`python -m build`。GitHub 来源、固定提交及设计取舍见 [upstream-review.md](docs/upstream-review.md)。工具代码 MIT；没有捆绑角色资产、SDK/Core、Editor 或令牌。GitHub 分发使用用户指定的私人仓库。


GitHub Actions 检查模板位于 [docs/github-actions-checks.yml](docs/github-actions-checks.yml)。当前 GitHub 登录缺少 workflow scope，未自动启用远端 CI；本机完整测试已执行。
