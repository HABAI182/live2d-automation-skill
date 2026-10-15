# 开发

使用 `pip install -e '.[dev]'`，运行 `ruff check .`、`pytest -q`。真实 Editor 测试用 LIVE2D_REAL_EDITOR=1 单独启用，并指定 LIVE2D_TEST_MODEL_UID。

更新单一能力源 `src/live2d_automation/capabilities.yaml`，附实际版本、后置条件、恢复证据与限制。协议夹具通过只证明逻辑，不得升级原生能力等级。新增 API 先查官方方法/字段，再做有界适配；不能将 SDK 当作 cmo3 作者工具。
