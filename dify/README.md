# 失物招领事务所

雨夜，柜台上有两把伞。玩家要说服管理员交出线索，再找出失主真正丢的那把伞。
管理员会把承诺和破案进度写进登记簿。关闭对话后，玩家可以用原存档码接着调查。

**Dify Chatflow** 解析玩家指令、调用存档服务、根据进度决定台词与线索、确认写入结果后回复。
**PowerContext** 用每个存档独立的 Scope 保存进度事件，通过 Source 采集和 Memory
读取实现持久化。服务重启后从原数据库读回。规则由 Dify Code 节点执行，
这版使用可复验的固定剧情和命令，无需配置模型、插件或外部 API Key。

## 在 Dify 中运行

适配目标为 [Dify 1.10.1](https://github.com/langgenius/dify/tree/1.10.1) 自托管版、
Python 3.12 与 PowerContext 冻结提交 `9db1753434162c0aa7c9f1189a1722e617880a52`。
需要可运行的 Docker 与 Compose。请从比赛仓库的 `dify/` 目录在 Bash 中执行：

```bash
export LOST_FOUND_DIR="$PWD"
export GAME_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
dify_root="$(mktemp -d)"
git clone --depth 1 --branch 1.10.1 https://github.com/langgenius/dify.git "$dify_root"
cd "$dify_root/docker"
cp .env.example .env
docker compose -f docker-compose.yaml -f "$LOST_FOUND_DIR/compose.dify.yml" up -d --build
```

首次部署后进入本机 Dify 管理界面并完成初始化。创建应用时选「导入 DSL 文件」，
导入本目录的 `lost-and-found.yml`。打开 Chatflow 的环境变量设置：

| 名称 | 值 |
| --- | --- |
| `GAME_API_URL` | `http://lost-found-memory:8787` |
| `GAME_API_TOKEN` | 与启动 Compose 时相同的值，作为 Secret 保存 |

应用预览里输入 `A7K9 查看`。无需发布到公网即可试玩。
存档服务只连接 Compose 的 `ssrf_proxy_network`，不映射端口。
代理配置仅允许该服务名及端口。若 HTTP 节点出现 403 或连接失败，
确认启动时带上两份 Compose 文件、令牌一致、`ssrf_proxy` 与 `lost-found-memory` 均已启动。
避免旋转正在使用的令牌，存档隔离标识由它和存档码共同派生。
共享环境使用高随机性的存档码，避免不同玩家有意或无意选到同一存档。

## 评审验证样例

初始条件：使用未使用过的存档码，例如 `A7K9`。已玩过时换新码，旧进度保留。

| 步骤 | 输入或操作 | 预期表现 |
| --- | --- | --- |
| 1 | `A7K9 调查` | 未承诺，管理员拒绝交出线索 |
| 2 | `A7K9 承诺` | 登记「先调查来历」 |
| 3 | 新建 Dify 对话，发 `A7K9 查看` | 读回承诺，管理员认出玩家 |
| 4 | `A7K9 调查` | 获得「与晴天的天空同色」线索 |
| 5 | 先发 `A7K9 回答红伞`，再发 `A7K9 回答蓝伞` | 答错可重试，答对获得徽章 |
| 6 | 新建对话并发 `B8L2 调查` | 独立存档从初始状态开始 |

在 Dify 节点运行详情中核对两个 HTTP 节点。也可在同一个部署终端查看 PowerContext
读写记录：

```bash
docker compose -f docker-compose.yaml -f "$LOST_FOUND_DIR/compose.dify.yml" logs lost-found-memory
```

如需验证重启持久化，步骤 2 后重启存档容器再执行步骤 3。保留原数据卷和令牌。

也可以发布 Chatflow 后在 Dify 的 API 访问页创建该应用的访问密钥，并执行
`DIFY_APP_API_KEY` 已配置的 `python3 verify_dify.py`。脚本调用 Dify 的 Chat API，
创建两个不同会话并用相同存档码读回承诺。每次执行自动选取新的测试存档码。
应用 API 地址可用 `DIFY_API_URL` 覆盖，默认 `http://localhost/v1`。

## 源码与本地验证

- `game.py` 是剧情规则，`python build_dsl.py` 将它嵌入 DSL。生成和验证脚本需要 PyYAML 6。
- `memory_server.py` 是 HTTP 到 PowerContext 的桥接服务。运行进度保存在 SQLite 持久化文件中的 PowerContext 数据里。
- `verification.log` 为 WSL 实跑记录。脚本执行已导出 DSL 内的 Python 节点，经真实 HTTP 调用 PowerContext，
  验证重启读回、错误选择、结局和存档隔离。它没有调用 Dify 引擎。

在现有 PowerContext Python 环境中可以运行 `python verify.py`。运行时需要安装对应版本的 PowerContext，
或把该提交的 `src/` 加到 `PYTHONPATH`。
本机验收使用冻结提交的源码与比赛的 Python 3.12 依赖环境。
已在 Windows Docker Desktop + WSL 2 上完成 Dify 1.10.1 的 DSL 导入、发布和 Chat API 验证。详见 [Dify API 实跑记录](dify-verification.log)。
