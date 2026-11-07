# 科研资料检索助手

基于 **OceanBase SeekDB (pyseekdb)** 的 Markdown 段落全文检索工具。
使用全文索引 (ik 分词) 实现搜索，并对返回结果做二次过滤以去除误匹配。

## 目录结构

```
qoder/
├── app.py                 # Flask 主程序
├── config.py              # 共享配置（数据库路径、依赖路径等）
├── verify_seekdb.py       # CLI 验证脚本（证明结果来自 seekdb）
├── seekdb/                # 搜索引擎模块
│   ├── __init__.py
│   ├── parser.py          # Markdown 段落解析器
│   └── store.py           # SeekDB：pyseekdb 全文检索存储
├── data/                  # 示例科研资料
│   ├── deep_learning_nlp.md
│   └── oceanbase_whitepaper.md
├── templates/
│   └── index.html         # 搜索页面模板
├── requirements.txt
└── .gitignore
```

## 环境要求

- Python 3.12+
- Linux 或 macOS（pyseekdb 嵌入式模式不支持 Windows）
- WSL 推荐

## 方式 A：干净 WSL 环境安装（推荐评审使用）

```bash
# 1. 进入 WSL
wsl

# 2. 安装系统依赖
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv

# 3. 进入项目目录
cd /mnt/f/oceanbase_competition/powerContext/codes/powercontext-qoder/qoder

# 4. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 5. 安装依赖（pyseekdb + flask）
pip install -r requirements.txt

# 6. 设置依赖路径环境变量（指向 pyseekdb 所在目录）
#    如果 pyseekdb 已安装在 venv 中，可跳过此步
export SEEKDB_DEPS=/path/to/pyseekdb/deps

# 7. 启动 Web 服务（首次启动自动索引 data/*.md 到 seekdb）
python app.py
```

浏览器打开 **http://localhost:5000** 即可使用。

数据库文件存储在 `~/seekdb_research_data` 目录下。

## 方式 B：复用现有环境（可选）

如果已有安装了 pyseekdb 的环境，可通过环境变量复用：

```bash
cd /mnt/f/oceanbase_competition/powerContext/codes/powercontext-qoder/qoder

# 设置 pyseekdb 依赖路径
export SEEKDB_DEPS=/mnt/f/oceanbase_competition/powerContext/codes/powercontext/minimax/.run/deps

# 使用现有 Python 环境启动
/mnt/f/oceanbase_competition/powerContext/codes/powercontext/competition/.venv/bin/python app.py
```

## CLI 验证（证明结果来自 seekdb）

```bash
cd /mnt/f/oceanbase_competition/powerContext/codes/powercontext-qoder/qoder

# 方式 A：使用 venv
source venv/bin/activate
export SEEKDB_DEPS=/path/to/pyseekdb/deps
python verify_seekdb.py

# 方式 B：复用现有环境
export SEEKDB_DEPS=/mnt/f/oceanbase_competition/powerContext/codes/powercontext/minimax/.run/deps
/mnt/f/oceanbase_competition/powerContext/codes/powercontext/competition/.venv/bin/python verify_seekdb.py
```

实际运行输出：

```
=== SeekDB (pyseekdb) Full-Text Search Verification ===
Database path: /home/liqiao/seekdb_research_data
Collection: research_paragraphs

Paragraphs in store: 22

Query: Transformer 预训练
------------------------------------------------------------
Results: 5 hits

[1] file=deep_learning_nlp.md  section=文本分类应用
    snippet: 基于<mark>预训练</mark>模型的文本分类方法通常包括两个阶段...

[2] file=deep_learning_nlp.md  section=摘要
    snippet: 深度学习技术近年来在自然语言处理（NLP）领域取得了显著进展...

[3] file=deep_learning_nlp.md  section=Transformer架构
    snippet: <mark>Transformer</mark>由Vaswani等人于2017年提出...

[4] file=deep_learning_nlp.md  section=机器翻译
    snippet: 神经机器翻译（NMT）已从早期的Seq2Seq架构演进到基于<mark>Transformer</mark>的架构...

[5] file=deep_learning_nlp.md  section=预训练语言模型
    snippet: BERT（Bidirectional Encoder Representations from <mark>Transformer</mark>s）由Google于2018年发布...

Query: 量子计算 超导
------------------------------------------------------------
Results: 0 hits
(no matches)

=== All results retrieved from OceanBase SeekDB via pyseekdb ===
```

**说明**：
- 查询 "Transformer 预训练" 命中 5 条结果，均来自 `deep_learning_nlp.md`
- 查询 "量子计算 超导" 命中 0 条（二次过滤去除了误匹配）
- 共索引 22 个段落，来自 2 个 Markdown 文件

## 网页搜索验证

启动 Web 服务后，访问 http://localhost:5000 并输入关键词：

| 查询 | 预期结果数 | 说明 |
|------|-----------|------|
| `Transformer 预训练` | 5 条 | 命中 deep_learning_nlp.md 中相关段落 |
| `量子计算 超导` | 0 条 | 无匹配，显示"未匹配到相关段落" |
| `分布式数据库 HTAP` | 1 条 | 命中 oceanbase_whitepaper.md |
| `Paxos 一致性` | 2 条 | 命中分布式事务章节 |

网页会显示：
- 命中结果数
- 每个结果的来源文件、章节、高亮摘要（原文已转义，防止 XSS）
- 零结果时显示"未匹配到相关段落"
- 检索出错时显示错误信息（与零结果区分）

**幂等性**：重复导入同一批资料，段落数保持不变（22 个段落）。索引前会清空集合，避免重复。

## 添加新资料

将 `.md` 文件放入 `data/` 目录，删除 `~/seekdb_research_data/.indexed` 后重启即可重建索引。

## 技术栈

- **pyseekdb** — OceanBase SeekDB Python SDK（嵌入式模式，版本 1.4.0.post1）
- **全文索引** — ik 分词器（通过 FulltextIndexConfig 配置）
- **搜索方式** — `collection.get(where_document={"$contains": query})` 全文匹配 + 二次过滤
- **Flask** — Web 服务框架
- **数据库位置** — WSL Linux 用户目录 `~/seekdb_research_data`
- **依赖配置** — 通过环境变量 `SEEKDB_DEPS` 指定 pyseekdb 所在目录

## 环境报错记录

### 问题 1：pyseekdb 嵌入式模式不支持 Windows
**原因**：pyseekdb 嵌入式模式仅支持 Linux 和 macOS。
**解决**：在 WSL 中运行。

### 问题 2：依赖下载过大（1.5GB+）
**原因**：pyseekdb 依赖 sentence-transformers + torch + NVIDIA CUDA 库。
**解决**：复用同级 `powercontext` 仓库中已安装的 `minimax/.run/deps` 目录（方式 B）。

### 问题 3：WSL Python 环境缺少 pip/venv
**原因**：WSL 中的 Python 是 externally-managed 环境。
**解决**：使用 `python3 -m venv` 创建虚拟环境（方式 A）。

## 参考链接

- [OceanBase SeekDB 官方文档](https://www.oceanbase.com/product/ai-database)
- [pyseekdb PyPI](https://pypi.org/project/pyseekdb/)
- [SeekDB 快速开始](https://dev.to/oug/get-started-with-seekdb-install-deploy-and-run-your-first-hybrid-search-2b31)
