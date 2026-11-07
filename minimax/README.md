# MiniMax × OceanBase 工单筛选演示

在已有工单查询中添加优先级筛选。有效规格在 `specs/priority.md`，基线实现是
`baseline/tickets.py`，新增功能和兼容性使用同一份 `tests/test_tickets.py` 验证。

## 初始化

从 PowerContext 仓库根目录在 WSL Bash 中执行。使用 Python 3.12 和 uv。

```bash
make -C competition install
UV_HTTP_TIMEOUT=180 uv pip install --python competition/.venv/bin/python \
  --target minimax/.run/deps pyseekdb==1.4.0.post1 pylibseekdb==1.4.0.post2
export PYTHONPATH="$PWD/src:$PWD/minimax/.run/deps"
competition/.venv/bin/python minimax/demo.py import
competition/.venv/bin/python minimax/demo.py context
```

预期上下文包含 `specs/priority.md`、`baseline/tickets.py` 中的
`list_tickets` 和 `tests/test_tickets.py` 中的三个测试。检索根据任务 ID 查询
seekdb 中的三个资料 ID，输出包含实际返回的原文及来源。独立验证规格更新：

```bash
competition/.venv/bin/python minimax/demo.py import --spec specs/priority-v2.md
competition/.venv/bin/python minimax/demo.py context
competition/.venv/bin/python minimax/demo.py import
```

第二条命令应输出 v2 中的大小写匹配规则。最后一条恢复此次代码修改采用的规格。

## 生成、恢复与验证

在已配置 `MINIMAX_API_KEY` 的同一个 WSL 窗口运行。
默认模型为 `MiniMax-M3.1-Flash`，中国区接口地址为 `https://api.minimax.cn/v1`。
`MINIMAX_MODEL` 和 `MINIMAX_BASE_URL` 可覆盖这些默认值。

```bash
competition/.venv/bin/python minimax/demo.py implement
```

基线预期有两项功能断言失败、一项兼容测试通过。随后 CLI 用查询得到的上下文调用
MiniMax，写入运行目录中的 `generated/tickets.py` 和 `tickets.diff`，并用
PowerContext 保存「待验证」状态。检查生成的 Python 代码和差异后，再启动另一个
CLI 进程：

```bash
competition/.venv/bin/python minimax/demo.py resume
```

预期从 PowerContext 读出待办、运行同一组测试并通过三项，然后把状态推进至完成。
再次运行 `resume` 应读到完成状态。

## 记录和复现

默认运行目录为 WSL 下的 `~/.local/state/powercontext-minimax/`，其中保存数据库
查询结果、脱敏的模型输入与生成代码、前后测试日志、
补丁和状态写入与读取记录。测试日志记录命令、源码 SHA-256 和退出码。提交材料应
从真实执行结果中选取这些记录与生成的代码快照。密钥、数据库和依赖目录留在本地。
换一个空的 `--run-dir` 就可以独立重跑，四条 CLI 命令须使用同一个运行目录。
嵌入式 seekdb 的运行目录应位于 WSL 的 Linux 文件系统，例如 `~/` 下。

seekdb 负责按任务 ID 检索工程资料，MiniMax 负责生成业务代码，PowerContext
负责持久化待办与验证结果。这个演示只处理工单优先级任务，工程资料使用精确 ID
查询，无需下载嵌入模型。模型输出可能不同，提交中的最终快照以测试结果为准。
