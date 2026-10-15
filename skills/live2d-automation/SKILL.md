---
name: live2d-automation
description: 通过本机 CLI/MCP 检查和编辑已有 Cubism 模型，执行参数/结构事务、透明度眨眼绑定、保存重开与原生导出，生成动作/表情，并用官方 SDK 验证。无需 computer-use；不负责从单图制作完整 Live2D 或桌宠。
metadata:
  version: "0.1.0"
---

# Live2D Automation

优先使用 `live2d_*` MCP；缺少连接时通过 `scripts/live2d.py` 调用已安装 CLI。先运行 doctor/capabilities，读取实际版本、Allow/Edit、模式及恢复标记。安装脚本保留独立 CLI 和 Skill，MCP 有 17 个类型化工具。操作与 JSON 示例见 [references/usage.md](references/usage.md)。

范围是已有模型的可验证自动化。模型仅作测试夹具；用户只要求 Skill 时，不扩展为角色美术、完整桌宠或桌面输入运行时。不要把创建参数/空变形器说成完成几何绑定。

先明确当前 UID、inspect/snapshot，再 plan。写工具默认 dry-run。用户已授权的动作直接转为 `confirm=true,dry_run=false`，带刚生成的 `snapshot_sha256`；结构编辑还使用唯一幂等键。遇到 UID/哈希改变，重新检查和规划，不重放写请求。每次原生写入检查版本、权限、Modeling、全局端口锁并备份最后保存的 CMO3。

当前编辑适配器为 Windows Cubism 5.4.00 alpha2 / API 1.1.0，能力为单机 pilot。有效期到 2026-10-18，过期阻止编辑，不绕过期限。5.3.04 / API 1.0.1 已实测基础连接/参数读写恢复，缺少新版结构与编辑 API；不要根据目录名称假定支持。未知新版本需独立适配和实测，不能延长旧适配器期限冒充兼容。

参数/组可创建、有限属性修改、移动和删除；绑定参数不自动删除。Part/Warp/Rotation 仅创建空容器。`snapshot_bindings` 读取每个对象的 Keyform 与公开属性，最多 64 个形状组合；不包含顶点坐标。眨眼配方只给已有眼睛/闭眼 ArtMesh 添加 0/1 参数键和透明度切换，可先 `cancel_trial=true` 验证取消恢复。它不会雕刻闭眼形状，也不会覆盖其他参数绑定。

保存重开/导出使用官方 Java Access Bridge 与白名单 Win32 快捷键，computer-use 调用为 0；会短暂占用 Editor 前台焦点。仅实测英文 UI、上述 Alpha2、Windows。首次启动用包内 `scripts/launch_editor.ps1 -InstallDirectory ...` 对该进程启用 JAB，不更改全局 Java 配置。授权/许可提示由用户完成；不自动操作 Allow/Edit。未知弹窗或焦点丢失就停止，不能用坐标点击猜测。保存只写新 CMO3；重开需要成功 save job，文件哈希和当前对象属性/绑定匹配；不会处理丢弃未保存内容的确认框。导出只写空的新目录，已有贴图集是前提。

生成 motion3/exp3 时使用真实模型参数范围，严格校验 ID、有限数值、时间、重复项；目前动作只生成线性曲线，Add/Multiply 表情范围按默认姿势检查。文件只能写配置输出目录，不覆盖已有动作/表情。可备份并更新同目录 model3 引用。返回文件校验通过后，仍需 `verify_runtime` 使用用户合法持有的官方 Framework/Core 加载；SDK 不随工具分发。

`validate_bundle` 只证明资源和部分 JSON；`verify_runtime` 另证明 Core 一致性、WebGL 实际渲染、参数/动作/表情在指定采样点的像素或顶点变化。SDK 验证依赖本机 Node、Vite、Playwright 和 Edge，安装脚本默认准备 Node 包。它不证明美术质量、完整时序或桌面键鼠交互。生成的 SDK 预览和报告是本地私有文件。

失败后看 job、备份、证据及恢复标记。事务内出错会取消并回读；不确定提交保留标记，阻止后续原生写入。`verify_changes` 支持结构和 binding job；`rollback_job` 支持结构、binding 与 UI job，只确认已恢复的状态，不猜测逆向修改。需要时先重开记录中的 original.cmo3，再显式提供新 UID；工具核对备份哈希和原结构/对象属性。未保存美术不在磁盘备份内，顶点不在属性快照内。UI 恢复还要求没有未知弹窗。不要删除 marker 假装恢复。

能力表是唯一真源。网格顶点雕刻、PSD 拆分/导入、模板拟合、物理创作、任意 GUI 操作目前不支持，应报告具体 capability gap。按用户范围完成已支持流程；不调用其他桌宠 Skill 扩大任务。本技能不授权公开上传模型、美术或工具仓库。

