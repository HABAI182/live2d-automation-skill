# 安装与使用

运行 scripts/install_windows.ps1 -RegisterMcp。默认建立本包虚拟环境、安装 wheel、以 npm ci 准备 SDK 检查器依赖、备份并安装独立 Skill、备份并注册同名 MCP。需要 Python 3.11+；SDK 验证器需要 Node 20.19+/22.12+ 和本机 Edge。-SkipRuntime 只保留 API/文件工具。没有下载 Editor、SDK 或浏览器。已有 MCP 连接不会热替换 Python 模块；重载 MCP 或在新会话发现 17 工具，CLI 可立即使用。

Cubism 打开合法的已保存测试 CMO3 副本，启用端口 22033 的外部集成，按 Editor 提示授予 live2d-automation Allow/Edit。工具不代替用户确认这些权限。5.3 可基础连接/参数读取；原生结构编辑只支持当前明确识别的 5.4 Alpha2/API 1.1.0。到 2026-10-18 后禁止该 Alpha 适配器写入，后续版本需要新适配。

GUI 文件功能要对 Editor 进程启用 Java Access Bridge。运行 `powershell -File scripts/launch_editor.ps1 -InstallDirectory 'C:/.../Cubism'`，完成首次许可提示并打开测试副本。脚本只传给新进程 JAVA_TOOL_OPTIONS，不修改安装或全局 Java 配置。当前 UI profile 是英文；其他语言/弹窗会明确停止。请在短暂保存/导出期间让 Editor 保持前台；失焦会停止输入。

流程和复制即用命令见 [Skill usage](../skills/live2d-automation/references/usage.md)。先 doctor/capabilities，再明确 UID 与模型范围。所有写工具默认 dry-run；执行须带 confirm、当前计划哈希。API 编辑、绑定、GUI 文件操作均使用按端口共享的跨进程锁。结构工具另有幂等键，重复键返回历史，使用 verify 读取现在状态。

可用范围：

| 工作 | 工具/CLI | 验证 |
|---|---|---|
| 连接、版本、权限与恢复诊断 | doctor/connect/list-documents | 实际进程、API 和文档 UID |
| 参数/组和空 Part/Warp/Rotation | plan/apply/verify/rollback | 事务内外结构/对象回读 |
| 属性/Keyform 检查与透明度眨眼绑定 | snapshot-bindings/blink | 目标属性、非目标不变、取消恢复 |
| 新副本保存、重开与原生 MOC3 导出 | editor-file | 原生文件哈希、重开新 UID、属性/绑定一致 |
| 线性 motion3 和 exp3 | generate-animation | 模型参数 ID/范围、严格时间/数值、JSON 回读 |
| 导出资源检查 | validate-bundle | 路径、非空资源和部分 JSON |
| 官方 SDK 加载与动作/表情检查 | verify-runtime | Core 一致性、WebGL、像素与指定顶点变化 |

导出要求现有贴图集，输出为新空目录。save 只另存新路径；reopen 只接受已验证 save job，核对文件哈希和当前属性/键。若用户在保存后又修改顶点，关闭可能弹出未保存提示；工具不会确认丢弃。API 公开属性不含所有顶点坐标，不能据属性一致宣称任意美术一致。

配置：LIVE2D_STATE_DIR 保存令牌、原生备份和审计；LIVE2D_OUTPUT_DIR 限制新文件/预览目录；LIVE2D_NODE_MODULES 指向 npm 依赖。锁/恢复标记始终在本用户 AppData 固定位置，自定义 state 不能绕过。生产令牌不在 ZIP，日志按字段脱敏，但本地证据仍可能含私人路径/结构。

失败恢复：rolled_back + error 表示动作失败但已确认恢复；cancel-trial 的 rolled_back + postconditions_passed=true 是成功测试。blocked + recovery 表示结果不确定，停止所有新原生写入。保留异常副本，重开任务报告中的 original.cmo3；调用 rollback，UID 改变时显式传新 UID。工具验证备份身份和结构/属性，不自动覆盖二进制或猜逆操作。UI 恢复还检查没有未知对话框。未保存美术不在最后磁盘备份内。

SDK/Core 不随包提供。使用合法的官方 Cubism SDK for Web，Framework 目录包含 src；Core 目录包含 JS 和相应 shaders。验证器用自带的原创页面与脚本复制必要资产到输出内的私有 runtime-checks；只监听回环，关闭浏览器和服务后留下 JSON/截图。结果证明指定采样点可加载/有变化；不代表艺术质量、所有动作时序或桌宠交互。官方参考：[SDK 模型加载](https://docs.live2d.com/en/cubism-sdk-manual/model-web/)、[Vite 安装条件](https://vite.dev/guide/)、[Playwright 浏览器 channel](https://playwright.dev/docs/browsers)。

尚不支持网格顶点雕刻、PSD 生成/导入、模板拟合、物理创作和任意菜单操作。完整桌宠不属于本 Skill 的验收对象。

