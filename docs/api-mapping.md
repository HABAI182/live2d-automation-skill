# API 映射与证据

所有请求使用 Version/RequestId/Type= Request/Method/Data；响应按 RequestId 关联，Event 不消耗响应等待者。注册与 Allow 查询使用基础 0.9.0 envelope，GetAPIVersion 探测按照官方样例单独使用 0.9.1（已在 5.3.04 实测），随后返回的版本决定读协议；原生编辑另要求 1.1.0 和明确的本机 Alpha2 版本证据。

| 高阶操作 | 官方方法 | 验证 |
|---|---|---|
| connect/doctor | RegisterPlugin, GetIsApproval, GetAPIVersion, GetIsEditApproval, GetCurrentEditMode | 真实回读；安装信息与监听进程单独记录 |
| documents | GetDocuments, GetCurrentModelUID | 显式目标 UID 与磁盘路径绑定 |
| snapshot/inspect | GetParameterStructure, GetPartStructure, GetDeformerStructure | 参数范围/KeyValues/重复 ID 和树结构；前后 UID |
| legacy inspect | GetParameterValues | current values only；complete_structure=false |
| parameter/group edits | AddParameter, EditParameter, DeleteParameter, MoveParameter, AddParameterGroup, EditParameterGroup, DeleteParameterGroup | 完整参数/组范围差异与非目标属性 |
| empty containers | AddPart, AddWarpDeformer, AddRotationDeformer | 两个对象树与 GetObject 属性 |
| native transaction | EditBegin(Silent=false), EditEnd(Cancel=false/true) | 提交前后/取消后结构与 UID |

`GetObject.Vertices` 若仅提供顶点数量，不当成网格坐标；本版不实现通用几何作者接口。树的 root 节点与 Editor 自动属性仍需要真实集成样例确认；不符合后置条件即停止或取消，不以适配猜测放宽验证。

调用成功与任务成功分开：Result 包括 tool_call_succeeded/postconditions_passed、作用范围、evidence 哈希、错误、后续动作。任务结构回读 verified 并不自动提高 registry 的版本成熟度。

## 5.4 Alpha2 实机差异

GroupName 规范化为 Name；Repeat/Type 规范化为 IsRepeat/IsBlendShape；GetDeformerStructure 的单一 %Root 列表规范化为树；GetObject.ParentId=%Root 与逻辑根对应。KeyValues 缺失时必须逐对象 GetParameterKeys 并验证范围/有限值；任一查询失败阻止编辑。MoveParameter 指定 InsertIndex=0，移动到根时采用当次快照读出的根组 ID。未知树格式仍拒绝处理。
