# Domain Docs

工程类技能在探索代码库时，如何消费本仓库的领域文档。

## Layout

本仓库是 **multi-context**（多上下文）布局：根目录放一份 `GLOSSARY-MAP.md`，由它指向各子项目自己的 `GLOSSARY.md`。

## Before exploring, read these

- 根目录的 **`GLOSSARY-MAP.md`**（存在时）：它指向每个上下文各自的 `GLOSSARY.md`，读与本次主题相关的那些。
- 单上下文仓库则直接读根目录的 **`GLOSSARY.md`**。
- **`docs/adr/`**：读与即将改动的区域相关的 ADR。多上下文仓库里，还要看 `src/<context>/docs/adr/` 下该上下文自己的决策。

这些文件不存在时就**安静地跳过**，不要提示缺失，也不要一上来就建议创建。`/domain-modeling` 技能（经由 `/grill-with-docs` 与 `/improve-codebase-architecture` 触达）会在真正写下词条或决策时按需创建。

## File structure

单上下文仓库（绝大多数仓库）：

```
/
├── GLOSSARY.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

多上下文仓库（根目录有 `GLOSSARY-MAP.md`）：

```
/
├── GLOSSARY-MAP.md
├── docs/adr/                          ← 系统级决策
└── src/
    ├── ordering/
    │   ├── GLOSSARY.md
    │   └── docs/adr/                  ← 该上下文自己的决策
    └── billing/
        ├── GLOSSARY.md
        └── docs/adr/
```

## Use the glossary's vocabulary

输出里出现领域概念时（issue 标题、重构提案、假设、测试名），用 `GLOSSARY.md` 里定义的词，不要漂移到术语表明确规避的同义词。

需要的概念术语表里还没有，是个信号：要么你在造项目不用的词（重新考虑），要么确实有缺口（记下来，留给 `/domain-modeling`）。

## Flag ADR conflicts

输出与已有 ADR 冲突时，明确指出来，不要悄悄覆盖：

> _与 ADR-0007（事件溯源订单）冲突，但值得重开，因为……_
