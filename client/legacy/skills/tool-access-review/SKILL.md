---
name: tool-access-review
description: Check available model tools and use the reviewed C+F command coordinator without confusing historical evidence with live calls.
---

This candidate is not installed into Codex's skill search path yet. A skill is
workflow guidance, not a tool registration mechanism or an executor.

At a fresh task, inspect the actual model tool directory. Use a native plugin
only when it is exposed and the requested read is authorized. Never infer model
exposure from CLI configuration, this skill, or a saved capability snapshot.

Use the reviewed immutable deployment path supplied by environment startup:

```text
python /APPROVED_VERSION_PATH/tool_access.py list-capabilities
python /APPROVED_VERSION_PATH/tool_access.py preflight --tool codegraph-local
python /APPROVED_VERSION_PATH/tool_access.py preflight --tool codegraph-remote
```

Only the fixed public repository/history read is implemented in this candidate:

```text
python /APPROVED_VERSION_PATH/tool_access.py invoke --tool github-history --operation read_verified_run --args '{"repository":"504309818/codegraph-public-runner-acceptance-20261106","sha":"c2f220778a402a706b8828d37ae8b3ad57224cf0","run_id":42246979456}'
```

The output verifies retrieval of historical F_BATCH evidence, not a new
CodeGraph status/search/explore invocation. Preserve verification_scope and
all NOT_VERIFIED fields. A successful status query never proves search works.

C_LOCAL is blocked until a safe executor is admitted; do not run the legacy
launcher or its whole-filesystem read-only mount. C_REMOTE is undeployed.
Never use an Actions run URL as a live MCP endpoint. Do not add secrets, URLs,
unapproved repositories/paths or commands to arguments. Do not index private
projects, create R2/F1 indexes, dispatch Actions, deploy or publish from this
skill. Those capabilities are not implemented or authorized here.

For a new Cloud task, record task identity, environment version, command result
and actual model tools again. Only a second real Cloud task can prove reuse.
