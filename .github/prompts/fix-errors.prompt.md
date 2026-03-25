---
description: "Read the FollowCursor error log and diagnose/fix the bugs found there"
agent: "agent"
---

Read the error log file at `%LOCALAPPDATA%/FollowCursor/error.log`.

For each distinct error in the log:

1. **Identify** the failing module, function, and line from the log entry
2. **Read** the relevant source file to understand the context
3. **Diagnose** the root cause — consider: race conditions, missing null checks, resource leaks, incorrect arguments, timeout issues, and encoder/codec failures
4. **Fix** the bug in the source code following the project's coding conventions
5. **Run tests** using the Run Tests VS Code task to verify the fix doesn't break anything

Group related errors (same root cause) into a single fix. Create a branch per fix per the project's branching policy.

The log format is:
```
<timestamp> | <module> | <level> | <message>
  File: <path>:<line>
  Function: <function_name>
<traceback if present>
```
