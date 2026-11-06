# Public-only CodeGraph runner acceptance

Standalone, generated public toy code and acceptance tooling. No private project,
account data, historical graph, credentials or external service is included.

The workflow is manual-only, one standard `ubuntu-24.04` job, maximum20 minutes,
with `permissions: {}`. It uses no checkout action, cache action, artifact upload,
repository secret, OIDC permission, external server or larger runner. The public
source is fetched by exact workflow commit SHA without authentication.

Node24.22.2 and CodeGraph1.6.2 artifacts are checksum-pinned in pins.json. The
hosted runner image itself cannot be pinned by this label; its actual image and
system-library hashes are recorded. A FROM-scratch worker contains only approved
public runtime files and this toy. No host workspace, HOME, token or Docker socket
is mounted. Actions control-plane temporary credentials may exist on the host;
this is not a claim that the entire VM is credential-free.

Before any CodeGraph invocation, controller inspection and worker canary,
environment, permission and network checks must pass. A static helper installs
an additional no-network seccomp filter and closes inherited descriptors. The
filter stacks with Docker's default policy rather than replacing it.

Only the fixed public toy is indexed. The first nonroot container gets a fresh
toy index bind writable. A second container mounts that same index read-only
and uses a read-only MCP engine. It performs actual initialize, initialized,
tools/list and status/search/explore calls, verifies toy symbols, source/index
identity and process exits. No authorization extends to other repositories.

Results are bounded public-toy JSON in job logs and a concise job summary. No
artifacts or images are uploaded. This is a candidate acceptance workflow, not a
claim that it has already passed on GitHub or that it exposes native model MCP.
GitHub must execute it after explicit approval. A failed safety gate stops index
and MCP phases; it must not be bypassed to obtain a passing result.

For multiple repositories, use separately approved immutable source snapshots,
per-job source/index identity, allowlisted tool/parameter mappings, isolated
workers and authenticated control-plane submissions. This public one-shot job
is a first-stage batch executor, not a persistent authenticated MCP endpoint.
