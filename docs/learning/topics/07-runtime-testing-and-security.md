# Topic: Runtime 测试与安全边界

> Day 7 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch4：4.8；Ch8：8.1～8.2 ｜ 外部参考：OWASP Agentic AI Security ｜ 状态：未开始

## 1. Engineering Question

Agent Loop 的测试如何避免真实调用 LLM？Tool 权限检查应放在 Dispatcher 还是 Runtime 层？Sandbox 的最小威胁模型是什么？

## 2. Primary Reading

- Book: Ch4：4.8；Ch8：8.1～8.2
- Chapter / Section: Ch4 4.8；Ch8 8.1～8.2
- 核心观点：

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| OWASP Agentic AI Security | | |
| 官方工程指南 | | |
| 框架 / 协议文档 | | |
| 论文 / 研究资料 | | |

## 4. Compare and Challenge

- 不同资料有哪些一致观点？
- 哪些观点存在分歧？
- 分歧来自应用场景、设计目标还是技术演进？
- 哪些结论缺少充分证据？
- 哪些观点不适用于当前项目？

## 5. Engineering Practice

- 实现了什么？
- Agent Loop 测试
- Tool 权限检查
- 基础 Sandbox Threat Model
- 为什么这样设计？
- 有哪些替代方案？
- 测试结果如何？
- 实践结果是否支持阅读材料中的观点？

## 6. Reflection

1. 今天最重要的知识是什么？
2. 哪个观点改变或修正了原来的理解？
3. 当前实现还有哪些局限？

## 7. Output

- Code / PR:
- Tests:
- ADR:
- Article Notes: docs/articles/agent-runtime.md、docs/engineering/security.md
- Unresolved Questions:
