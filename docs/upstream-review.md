# GitHub 复用与设计取舍

查询与本地源码核对日期：2026-10-15。以下项目均固定了审查提交；工作目录中的第三方 checkout 不包含在交付包中。

| 来源 | 审查提交 | 吸收的设计 | 处理方式 |
|---|---|---|---|
| [Live2D 官方 EditSample](https://github.com/Live2D-Garage/CubismExternalAppPluginSamples/tree/54alpha/04_EditSample) | `390403036a22b8528847e002992addf673f6d003` | WebSocket envelope、注册、编辑授权、EditBegin/EditEnd | 按官方协议独立实现，保留来源与 MIT 声明 |
| [CubismExternalEditMCP](https://github.com/nana7chi/CubismExternalEditMCP) | `863ebc87d0feafd841cf3877b16f0311047b9448` | 请求 ID 映射、Token 缓存、参数与对象方法映射 | 协议交叉核对；未复制 42 工具或整个客户端 |
| [SolidWorks Automation Skill](https://github.com/wzyn20051216/solidworks-automation-skill) | `a3ddc5694161299b50b9e9f688fcd6f0bf815da2` | 单一能力表、确定性恢复决策、追加 Trace、Golden Workflow 的验收结构 | 迁移设计思想；不引入 CAD 后端或其大量领域代码 |
| [J621111/live2d-automation](https://github.com/J621111/live2d-automation) | `4ad77c555165e1ba02bd68e0db7299d5f4fe0327` | 阶段证据、输出目录约束、故障场景和 UI profile 的显式能力缺口 | 概念参考；审查提交未发现 LICENSE，因此不复制源代码。README 自述导出为 mock intermediate，不采用其原生导出结论 |

具体检查了官方 `ceplugin.py`、第三方 `cubism_mcp.py`，SolidWorks 的 `scripts/core/{capability,recovery,trace}.py` 与 golden-workflows，以及实验项目的 README/会话接口组织。

针对 Live2D 的改进：

- 不自动重连并重放写请求。请求超时不能证明 Editor 没有执行。
- 写锁按本机 Editor 端口跨进程共享；自定义证据目录不能绕过锁。
- UID 锁与计划快照哈希同时检查；合法的旧计划也不能修改已经变动的模型。
- 操作成功响应后仍核对目标及非目标结构，提交前后分别回读。
- 不确定提交不做逆向“猜测恢复”；保留备份、恢复标记和失败原因。
- 能力成熟度与某一次任务回读结果分开。契约夹具仅在 tests 中存在，正常 CLI 没有 mock native backend。
- 缩小首版结构创建范围：只创建空 Part/变形器，不自动改父子绑定，不声称雕刻 Keyform。

官方版本与协议基准：

- [5.4 Alpha 公告](https://www.live2d.com/en/information/cubism-5_4-alpha/)：本次查询的 Alpha2 可用期到 2026-10-18。
- [编辑 API 手册](https://cubism.live2d.com/editor-alpha/doc/manual/alpha1/zh/external-api-intergration/index.html)：Modeling、Edit 授权与事务要求。
- [基础 API 一览](https://docs.live2d.com/en/cubism-editor-manual/external-application-integration-api-list/)：注册、文档 UID 和基本读取协议。

版本元数据是本机诊断证据，不是安全签名认证。未来稳定版本必须新增适配与真实测试，不能简单延长 Alpha 截止日期。
