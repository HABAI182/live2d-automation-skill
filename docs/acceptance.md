# 0.1.0 验收报告

日期：2026-10-15。验收对象是 **Live2D Automation Skill + CLI + 17 个 MCP 工具**。已有模型仅用于私有测试，没有实施完整桌宠。能力等级为限定环境的 pilot，完成设计书 v0.1 的已有模型闭环。

实机环境：Windows、Cubism 5.4.00 alpha2 / API 1.1.0、英文 UI，用户 Allow/Edit 已授权。5.3.04 另有基础连接/参数读取与临时恢复记录。原始角色工程的磁盘 SHA256 未改变。

| 验证项目 | 结果 | 证据范围 |
|---|---|---|
| 参数/组生命周期及空 Part/Warp/Rotation | 通过 | 创建、属性/结构回读、提交、修改、移动、删除、取消恢复 |
| 眨眼透明度绑定 | 通过 | 0/1 参数 Keyform、ArtMesh 属性、非目标不变、25% 测试修改取消与独立回读 |
| 真实 CMO3 保存副本 | 通过，约 4.92 秒 | 新文件、哈希、属性/绑定保持 |
| 关闭后重开保存副本 | 通过，约 5.14 秒 | 文档路径、新 UID、属性与所有读取到的键一致 |
| 真实 Editor 导出 MOC3 | 通过，约 10.63 秒 | 原生二进制、model3、贴图资源 |
| 生成 3 个 motion3 和 1 个 exp3 | 通过 | 参数 ID/范围、曲线时间、元数据、JSON 回读、model3 引用备份/更新 |
| 官方 SDK/Core 与 WebGL | 6/6 通过 | MOC 一致性、27 参数、10 drawable、非空画面；直接参数、三动作、表情的实际像素/顶点及采样参数值 |
| 安装后真实 stdio MCP 流程 | 通过 | 17 工具均有类型化输出；19 次实际调用完成上面的闭环 |
| 常规测试 | 75 passed, 1 skipped | 单元、契约、故障注入、真实 stdio 协议；跳过项是需主动开启的 Editor 测试 |
| 独立真实 Editor 读取测试 | 1 passed | 另开连接读取真实模型快照 |
| ruff / mypy / Skill validator / 构建 | 通过 | 静态检查、Skill 基础完整性、sdist/wheel |
| wheel 与源码文件比对 | 22/22 相同 | 含 Python、能力表、SDK 验证页面和脚本 |

所有 Editor 操作的 computer-use 调用数为 0。参数/结构/属性通过官方 WebSocket；保存重开与导出通过官方 Java Access Bridge 加 PID/焦点约束的白名单快捷键，不依赖截图坐标，但需要短暂前台焦点。

可复用实测脚本是 scripts/real_file_workflow.py，须显式 --execute-on-test-copy，并提供当前 UID、合法测试副本、模型匹配的 recipe/animation、用户持有的官方 Framework/Core。它调用安装后的真实 stdio MCP，没有 Mock 入口。示例在 examples/，命令见使用指南。

机器证据：

- evidence/cubism54-workflow.json：最终安装版 19 次 MCP 调用和 6 项官方 SDK 结果，路径已脱敏；私有原始报告保留在本机，并记录原始报告 SHA256。
- evidence/installation-v010.json：22 个文件哈希、17 工具输出 schema 和实机快照校验。
- evidence/tests-v010.xml、real-read-v010.xml：常规和独立实机测试。
- evidence/cubism54-native-smoke.json、cubism54-parameter-lifecycle.json：早期创建/取消和三次提交生命周期。

本轮发现并修正：MCP 的标准输入不得被 SDK 检查子进程继承；改为 DEVNULL 后安装版完整复测通过。官方 SDK 异步 shader 编译需等待真实非空画面。motion3 的 FadeInTime/FadeOutTime 放入 Meta，并以采样点参数值再验。未知弹窗、打开的菜单和失焦会停止文件快捷键。失败/超时保留恢复信息，禁止把成功响应当成全部验收。

边界：

- 只在上述单机/版本/语言验证。Alpha2 有效期到 2026-10-18，适配器到期禁写，不能假定未来版本兼容。
- 透明度配方需要已有闭眼美术；不雕刻顶点，不代表美术质量合格。API 对象快照不含顶点坐标；磁盘备份不含未保存美术。
- 保存只写新副本；重开只接受已验证 save job；导出要求已有贴图集和新空目录。UI 不处理覆盖/丢弃修改确认，也不替用户授予 Allow/Edit。
- SDK 验证仅覆盖指定采样点。没有验收所有动作时序、艺术质量、桌宠原生窗口或键鼠交互。
- PSD 生成/导入、模板拟合、物理创作、自由网格编辑及任意 Editor 菜单控制不在 v0.1 范围。

GitHub 上传按用户要求使用私人仓库，只上传工具源码、Skill、示例和脱敏证据。角色资产、原生模型、官方 SDK/Core、Editor、令牌和本机运行配置不进入仓库。


远端 Actions 未运行：当前 OAuth 具备 repo，缺少 workflow scope。CI 配置保留为 docs/github-actions-checks.yml 模板；本次 75+1 项验证均为本机实际执行。
