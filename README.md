# Agent Research Lab

[![CI](https://github.com/BillZong/agent-learning-coach/actions/workflows/ci.yml/badge.svg)](https://github.com/BillZong/agent-learning-coach/actions/workflows/ci.yml)

The lab is built from scratch, we don't want to use complicated libraries like LangChain, LangChain, etc. For now, we'll just use OpenAI and Anthropic APIs, sometimes litellm is enough to achive the same result.

Read more architecture and dicisions on `docs/`.

## Development / 开发

```bash
make install-dev   # 安装依赖（uv + dev 工具链）
make ci            # 推送前跑这个：与 CI 工作流完全一致
```

`make ci` = 格式检查 + 静态检查 + 类型检查 + 测试（含覆盖率门槛）+ 评估套件 + 构建。

## Demo / 演示

**一条命令跑完整学习流程**：

```bash
uv run python examples/learning_journey.py          # 离线（脚本化模型，确定）
uv run python examples/learning_journey.py --live   # 真实模型（需要 .env 凭证）
```

八步，每一步的产物都打印出来：

| 步 | 做什么 | 对应 |
| -- | ------ | ---- |
| 1 | 导入课程材料 → 切分 → 向量库 | Day 27–30 |
| 2 | 建立学习状态（目标 / 进度 / 画像） | Phase 1 |
| 3 | 检索材料（带引用标记） | Day 29 |
| 4 | 装配上下文（五段 + 优先级） | Day 32 |
| 5 | 直接教学（讲解 + 分步示例 + 引导问题） | Day 33 |
| 6 | 引用校验（编造的引用会被拦下） | Day 31 |
| 7 | 苏格拉底式追问（判断状态 → 调整策略） | Day 34 |
| 8 | 兜底话术（被拦下时学习者看到什么） | Day 31 |

**其他可跑的入口**：

```bash
uv run python examples/reliability_report.py   # 评估套件（CI 每次都跑）
uv run python examples/rag_citation_agent.py  # 检索 + 引用 + 校验的闭环
uv run python examples/tutor_context.py       # 上下文的装配与取舍
```

**示例是项目最好的文档，所以它们每个都被 CI 冒烟测试跑一遍**
（`tests/test_examples.py`）——示例坏了，CI 就红。

**本地跑的命令必须和 CI 跑的一致。** 这条是被现实教会的：CI 的
`ruff format --check .` 覆盖整个仓库（包括 Markdown 里的 Python 代码块），而本地
只跑了 `src/ tests/ examples/`，于是 CI 连着四次红灯没人发现。

| 命令 | 作用 |
| ---- | ---- |
| `make fmt` / `make fmt-check` | ruff format（写入 / 只检查） |
| `make lint` | ruff check |
| `make typecheck` | mypy（`src/`） |
| `make test` / `make test-cov` | pytest（`test-cov` 带覆盖率门槛 90%） |
| `make eval` | 评估套件（行为验收，失败非零退出） |
| `make check` | 提交前的全量检查 |
| `make ci` | **与 CI 一致**（推送前跑这个） |

可选：`uv tool install pre-commit && pre-commit install`——把格式与静态检查挪到
提交之前（配置见 `.pre-commit-config.yaml`）。

质量门槛的说明见 [`docs/engineering/evaluation.md`](docs/engineering/evaluation.md)。
