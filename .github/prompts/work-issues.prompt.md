---
description: "Fetch open GitHub issues not being worked on, pick which ones to implement, and start working on them in parallel"
agent: "agent"
argument-hint: "Optional: filter by label (e.g. 'bug', 'enhancement') or leave blank for all"
---

You are a developer on the FollowCursor project. Your job is to pick up unworked GitHub issues and implement them.

Read [copilot-instructions.md](../copilot-instructions.md) for project context.
Read [implement-issue.instructions.md](../instructions/implement-issue.instructions.md) for implementation conventions.

## Step 1 — Fetch available issues

Run `gh issue list` in the terminal to find open issues that nobody is working on:

```
gh issue list --state open --json number,title,labels,assignees,body --limit 30
```

Filter the results to issues that are **not assigned** to anyone. If the user provided a label filter in their argument, also filter by that label.

## Step 2 — Present issues for selection

Show the available issues in a table:

| # | Title | Labels |
|---|-------|--------|

Use `vscode_askQuestions` to ask the user which issues they want to work on. Allow selecting multiple issues. Ask whether they want to tackle all of them or pick specific ones by number.

Wait for the user's response before proceeding.

## Step 3 — Plan the work

For each selected issue:

1. Read the full issue body (`gh issue view <number> --json body,title,labels`)
2. Determine affected source files by searching the codebase
3. Decide the branch name following the convention: `fix/...`, `feat/...`, or `refactor/...`

Present a brief implementation plan for each issue and confirm with the user.

## Step 4 — Implement using subagents

Create a **separate branch** for each issue from `main`.

For each selected issue, use `runSubagent` to delegate the implementation. Give each subagent a detailed prompt containing:

- The issue number, title, and full body
- The branch name to create (from `main`)
- The list of affected source files you identified
- A reminder to follow [implement-issue.instructions.md](../instructions/implement-issue.instructions.md) conventions
- Instructions to: read relevant files → implement the change → add/update tests → run tests → commit with `<type>: <description> (#<issue-number>)`

Each subagent works on its own branch, so there are no merge conflicts between them. After each subagent finishes, return to `main` before starting the next: `git checkout main`.

## Step 5 — Summary

After all subagents finish, present results:

| # | Issue | Branch | Status | Notes |
|---|-------|--------|--------|-------|

For any failed implementations, report what went wrong and suggest next steps.
