# CLI 与 MCP 调用

安装脚本将真实 Python 和 Node 模块目录写入 Skill runtime.json。后备入口是 `python scripts/live2d.py ...`，或仓库内 `.venv/Scripts/live2d-automation.exe ...`。全局选项 `--state-dir`、`--output-dir`、`--port` 放在子命令前。输出路径统一使用正斜线相对路径。

先 doctor → capabilities → inspect/snapshot。结构示例：

```powershell
python scripts/live2d.py plan --uid '<UID>' --ops operations.json
python scripts/live2d.py apply --uid '<UID>' --ops operations.json --key tap-001 --execute --confirm --snapshot-hash '<PLAN_HASH>'
python scripts/live2d.py verify --job job-tap-001
```

operations.json 是 Operation 数组，例如：
```json
[{"kind":"create_parameter","id":"ParamTap","name":"Tap","min":0,"default":0,"max":1}]
```

支持 create/update/delete_parameter、move_parameter、create/update/delete_group、create_part/warp/rotation。name 修改是显示名称，不能改 ID。参数自身不会产生可见变形；空变形器也不会自动包含对象。

绑定先读取 `snapshot-bindings --uid ...`，再使用配方，mesh ID 必须来自当前模型，不能猜：
```json
{"kind":"blink","sides":[{"parameter":"ParamEyeLOpen","eye_mesh":"EyeOpen","lid_mesh":"EyeClosed"}]}
```

```powershell
python scripts/live2d.py blink --uid '<UID>' --recipe blink.json
python scripts/live2d.py blink --uid '<UID>' --recipe blink.json --execute --confirm --cancel-trial --snapshot-hash '<PLAN_HASH>'
```

cancel-trial 成功返回 rolled_back、postconditions_passed=true。正式保留绑定时重新 plan，去掉 cancel-trial。失败后恢复也会返回 rolled_back，但带 error，不能当任务成功。

原生文件：
```powershell
python scripts/live2d.py editor-file --uid '<UID>' --action save --path copies/Model.cmo3
python scripts/live2d.py editor-file --uid '<UID>' --action save --path copies/Model.cmo3 --execute --confirm --snapshot-hash '<PLAN_HASH>'
python scripts/live2d.py editor-file --uid '<UID>' --action reopen --save-job '<SAVE_JOB>'
python scripts/live2d.py editor-file --uid '<UID>' --action export --path export/Model.moc3
```

reopen/export 同样先 plan，再加 execute/confirm/hash。重开后使用返回的新 UID。导出目录必须为空且模型已有贴图集。仅英文 Alpha2 UI；JAB 未启动时运行包内 launch_editor.ps1 启动新的 Editor。不要自动关闭用户生产文档或处理丢弃修改提示。

文件生成：
```powershell
python scripts/live2d.py generate-animation --uid '<UID>' --spec blink-motion.json --path export/motions/Blink.motion3.json --model export/Model.model3.json
python scripts/live2d.py generate-animation --uid '<UID>' --spec blink-motion.json --path export/motions/Blink.motion3.json --model export/Model.model3.json --execute --confirm --snapshot-hash '<PLAN_HASH>'
python scripts/live2d.py verify-runtime --options runtime-options.json
```

仓库 examples 包含 motion、expression、binding 和 runtime-options。生成时 --model 可选；指定时备份并添加 model3 引用，不覆盖同名动作组/表情。动作是 0 到 duration 严格递增的线性点，每个点必须在当前参数范围内。

runtime-options 需要输出目录内 model3 相对路径、用户合法持有的 Framework（包含 src）和 Core（包含 live2dcubismcore.min.js；新版还需 shaders）目录，以及可选 cases。cases 可以设置参数、采样一个动作或表情；默认要求实际像素变化，可额外 require_geometry_change。安装脚本配置 LIVE2D_NODE_MODULES；使用源码时也可 npm ci --prefix runtime --ignore-scripts。

恢复使用 `rollback --job <job-/binding-/ui-...> [--uid <NEW_UID>]`。历史结构幂等回放不是当前状态证明，应 verify。未知/不确定提交保留 marker，先按报告重开原生备份，再核对恢复。不要猜逆操作或删 marker。

