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
