# Issue tracker: GitHub

本仓库的 issue 与规格都放在 GitHub Issues 里，所有操作走 `gh` CLI。仓库：`zhangjunhao-910122/ccs`。

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`。多行正文用 heredoc。
- **Read an issue**: `gh issue view <number> --json number,title,body,labels,comments`。
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，配合 `--label` 与 `--state` 过滤。
- **Make an issue a sub-issue of a parent**: `gh issue create --parent <parent> ...`，或事后 `gh issue edit <parent> --add-sub-issue <child>`（`gh` 2.94+）。旧版 `gh`：`gh api --method POST repos/<owner>/<repo>/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>`（数据库 id，见下面 **Blocking**）。不支持 sub-issue 时，在子票正文顶部写 `Part of #<parent>`。
- **Blocking**: GitHub 原生 issue 依赖，规范且界面上可见。用 `gh issue create --blocked-by <n>,<n> ...` 或 `gh issue edit <child> --add-blocked-by <blocker>`（`gh` 2.94+）。旧版 `gh`：`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，其中 `<blocker-db-id>` 是阻塞票的数字 **database id**（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`，不是 `#number`，也不是 `node_id`）。依赖功能不可用时，退回在子票正文顶部写 `Blocked by: #<n>, #<n>`。所有阻塞票都关闭时，该票才算解除阻塞。
- **Count open blockers**: `gh api repos/<owner>/<repo>/issues/<n> --jq .issue_dependencies_summary.blocked_by`（只算未关闭的；0 表示可开工）。`gh issue view <n> --json blockedBy` 也会列出已关闭的阻塞票，用 `--jq '[.blockedBy.nodes[] | select(.state == "OPEN")] | length'` 过滤。
- **Comment on an issue**: `gh issue comment <number> --body "..."`。
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`。
- **Close**: 先 `gh issue comment <number> --body "..."` 说明原因，再 `gh issue close <number>`。对已关闭的 issue，`gh issue close --comment` 会丢掉评论但仍以 0 退出。

在克隆目录里运行时 `gh` 会自动从 `git remote -v` 推断仓库。

只往用户自己的仓库提 issue，绝不提到 `mattpocock/skills`。在 fork 里 `gh` 可能默认指向上游父仓库，所以会话里第一次 `gh issue create` 之前先用 `gh repo view --json nameWithOwner` 确认目标。

## Pull requests as a triage surface

**PRs as a request surface: no.** _（若本仓库把外部 PR 当作需求来源，改成 `yes`；`/triage` 会读这个开关。）_

设为 `yes` 时，PR 与 issue 走同一套标签和状态，用对应的 `gh pr` 命令：

- **Read a PR**: `gh pr view <number>`，再看 `gh pr view <number> --comments`（非 TTY 下 `--comments` 只打印评论，不打印正文），差异用 `gh pr diff <number>`。
- **List external PRs for triage**: `gh api --paginate 'repos/{owner}/{repo}/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`。
- **Comment / label / close**: `gh pr comment`、`gh pr edit --add-label`/`--remove-label`、`gh pr close`。

GitHub 的 issue 与 PR 共用一个编号空间，所以裸写 `#42` 可能指向 PR：先 `gh pr view 42`，失败再 `gh issue view 42`。

## When a skill says "publish to the issue tracker"

创建一个 GitHub issue。

## When a skill says "fetch the relevant ticket"

按上面 **Read an issue** 读取。

## Wayfinding operations

供 `/wayfinder` 使用。**地图**是一张带 `wayfinder:map` 标签的 issue，**子票**是挂在它下面的 issue。

- **Map**: 单张带 `wayfinder:map` 标签的 issue，正文承载 Notes / Decisions-so-far / Fog 三段。`gh issue create --label wayfinder:map`。
- **Child ticket**: 作为 GitHub sub-issue 挂到地图上（见 **Make an issue a sub-issue of a parent**）。sub-issue 不可用时，在 map 正文里加任务清单，并在子票正文顶部写 `Part of #<map>`。标签用 `wayfinder:<type>`（`research`/`prototype`/`grilling`/`task`）。被认领后，assignee 就是推进它的开发者。
- **Blocking**: 见 Conventions 里的 **Blocking**。
- **Frontier query**: 列出地图下未关闭的子票（`gh issue list --state open`，限定在 map 的 sub-issue / 任务清单里），剔除有未关闭阻塞票的（见 **Count open blockers**，或 `Blocked by` 行里的未关闭 issue）和有 assignee 的；按 map 顺序取第一张。
- **Claim**: `gh issue edit <n> --add-assignee @me`，这是本会话的第一次写操作。
- **Resolve**: `gh issue comment <n> --body "<answer>"`，再 `gh issue close <n>`，然后把「要点 + 链接」追加到 map 的 Decisions-so-far。
