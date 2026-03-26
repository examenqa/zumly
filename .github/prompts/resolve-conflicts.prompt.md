---
description: "Resolve merge conflicts on pull requests by rebasing onto main, fixing conflicts, running tests, and force-pushing"
agent: "agent"
argument-hint: "PR number(s) to fix, e.g. '25' or '25 28 31', or 'all' for every conflicted PR"
---

You are resolving merge conflicts on open pull requests for this repository.

Read [copilot-instructions.md](../copilot-instructions.md) for project context.

## Step 1 — Identify conflicted PRs

If the user specified PR numbers, use those. If they said "all", run:

```
gh pr list --state open --json number,title,headRefName,mergeable --limit 50
```

Filter to PRs where `mergeable` is `"CONFLICTING"`. Present the list:

| # | Title | Branch | Mergeable |
|---|-------|--------|-----------|

If no PRs have conflicts, report that and stop.

## Step 2 — Ensure main is up to date

```
git checkout main
git pull origin main
```

## Step 3 — Rebase each conflicted PR

For each PR, perform these steps **sequentially** (not in parallel — each rebase changes the working tree):

1. **Check out the branch**:
   ```
   git checkout <branch-name>
   ```

2. **Start the rebase**:
   ```
   git rebase origin/main
   ```

3. **Find conflicts** — search for conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) in the conflicted files.

4. **Understand both sides** — read the surrounding code to understand:
   - What `HEAD` (main) changed and why
   - What the PR branch changed and why
   - Whether both changes can coexist or one supersedes the other

5. **Resolve conflicts** — edit each conflicted file to produce the correct merged result:
   - Keep changes from **both sides** when they don't contradict
   - Use main's newer APIs/helpers (e.g. renamed functions, new patterns) combined with the PR's feature logic
   - Never leave conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) in the file

6. **Verify no markers remain**:
   ```powershell
   Select-String -Path "<file>" -Pattern "<<<<<<|======|>>>>>>" | Measure-Object | Select-Object -ExpandProperty Count
   ```
   The count must be `0`.

7. **Stage and continue**:
   ```
   git add <conflicted-files>
   git rebase --continue
   ```
   If additional conflicts appear in subsequent commits, repeat steps 3–7.

8. **Run tests** using the **Run Tests** VS Code task (do not run pytest manually). All tests must pass.

9. **Force-push** the rebased branch:
   ```
   git push --force-with-lease origin <branch-name>
   ```

10. **Return to main**:
    ```
    git checkout main
    ```

## Step 4 — Summary

After processing all PRs, present results:

| # | Title | Branch | Result | Notes |
|---|-------|--------|--------|-------|

For each PR, report whether the rebase succeeded, how many conflicts were resolved, and whether tests passed.

## Important rules

- Always use `--force-with-lease` (not `--force`) when pushing
- Never skip tests — a green test suite is required before pushing
- If a rebase produces conflicts you cannot confidently resolve, abort with `git rebase --abort` and report the issue
- Process PRs one at a time — return to `main` between each
- Do not modify code beyond what is needed to resolve the conflict
