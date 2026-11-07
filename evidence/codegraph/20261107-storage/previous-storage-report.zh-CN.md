# CodeGraph 报告与证据保存权限验收报告

日期：2026-11-07，Asia/Shanghai。最终持久化状态：**EVIDENCE_NOT_PERSISTED**。

本轮已定位正式输出目录的权限故障，并在当前实例的受控工作区完成候选证据保存、独立进程读取、摘要复核和固定版本离线恢复。没有获得跨 Cloud 任务持久存储或新任务读取的真实证据，不能报告持久化交付完成。未重新安装或重跑 CodeGraph 公开玩具验收。

## 1. 权限故障根因及原始证据

目标路径：`/codex/browser/projectless/0010-1-codegraph-c-f-codegraph-cf-test-codegraph-clou/output`。

本轮仅进行一次有系统调用跟踪的目标目录创建探测：

```text
strace -f -qq -e trace=mkdir,mkdirat -o /workspace/work/cf-storage-audit-20261107/mkdir.strace mkdir -p /codex/browser/projectless/0010-1-codegraph-c-f-codegraph-cf-test-codegraph-clou/output

mkdir("/codex", 0777) = -1 EACCES (Permission denied)
```

准确失败点是创建第一级 `/codex`，errno=13，返回值=-1；后续各级路径尚未创建。当前真实、有效、保存及文件系统 UID/GID 均为 1000:1000，用户为 agent；CapEff=0。现存父目录 `/` 的所有者为 root:root，权限 0755，当前用户有效写权限为 false，未观察到 POSIX access ACL。`/codex` 不存在。

`findmnt` 和 `/proc/self/mountinfo` 显示 `/` 是 `overlay`，挂载选项为 `rw,relatime`；`/workspace` 位于同一根 overlay，没有单独的 `/workspace` 或 `/codex` 挂载。故本次错误是普通目录访问控制不足，不是只读文件系统 EROFS。受控根目录无法由普通任务用户创建新的顶级目录。没有证据证明这里存在已挂载的专用“平台输出卷”。

命令工具的文件系统配置为 unrestricted；进程 Seccomp=2，存在一个过滤器，但此值不能指明某条拒绝规则。已观测的根目录 DAC 权限足以解释 EACCES，不将故障臆断为额外的路径沙箱规则。上轮 C 自身 bubblewrap 中 `/app/_cf_state` 的 EROFS 是另一项限制，不能与本次 `/codex` 的 EACCES 混同。

本轮没有提权、没有 chmod/chown 系统目录、没有使用容器修改宿主路径，也没有反复重试不可写目标。

## 2. 候选保存位置与实际修复结果

候选位置：`/workspace/work/cf-storage-audit-20261107/payload`。

`/workspace` 属于 1000:1000，权限 0755；`/workspace/work` 和本轮审计目录属于 1000:1000，权限 0700。父路径没有符号链接。实际文件创建和读取成功，验证不只依赖 os.access。

采用的处理是在现有权限内改用当前任务工作区，保留不可写的正式目标不变。它解决了本实例的候选落盘问题，没有修复或取得正式平台输出目录的权限。

候选目录包含：本中文报告；permission-diagnostics.json 和 mkdir.strace；environment-status.json；prior-audit 下原始测试返回、请求、原始工具响应、证据清单和原始证据包；fixed 下固定版本信息及自包含 Git bundle；本轮命令记录、保存/读取结果、离线恢复结果、回滚记录及持久化判定。封装后另生成逐文件 SHA256SUMS 和候选证据包，文件清单以 SHA256SUMS 为准。

上轮证据清单的 21 个条目全部重新核对一致，23 个现有文件逐字节复制后摘要一致。上轮原始证据包摘要仍为：

```text
a72941c6d92ae36af165d4e5103807a6fce2940da747cfd0f42089b1b1804065
```

## 3. 固定版本与本地恢复验证

| 对象 | 冻结提交 |
|---|---|
| C 客户端 | 9a8a611c515635e74072d165f493bf248a3cf545 |
| F 封装 | b94866f8a57311ec72d34c432ea3fccf70862fa0 |
| 实际执行源码 | c2f220778a402a706b8828d37ae8b3ad57224cf0 |

公开来源为 `504309818/codegraph-public-runner-acceptance-20261106`。C manifest SHA256 为 `5dd8975db10dae1c0119189f02f42ee4d3e45fa960fb9cc6ddeace5f0c72a67a`。

仅从该公开仓库现有本地对象复制三个冻结提交到本轮临时 bare 仓库，创建自包含 `fixed/frozen-versions.bundle`。未 fetch 网络、未推送、未改变原仓库 refs。bundle 校验成功；另一独立本地 bare 仓库从 bundle 恢复成功，三个 ref 的完整 SHA 一致，并复核 C 的 15 文件和实际执行源码的 12 文件摘要，共 27 文件。该过程是离线对象恢复，没有执行 C/F、索引或 MCP 查询。

bundle SHA256：

```text
59cd5a4d148a31501c06cafea9430ec73814ce1e8ee66d20921faf2871795c35
```

本地保存探针执行了文件 fsync、父目录 fsync，随后由 `/usr/bin/python3 -I -B` 新进程重新读取并计算 SHA256，结果 PASS。探针摘要为：

```text
eb67f3ec07489458c9195d7a55582dfb882331760a471672ce50b0f2a06d5b88
```

封装阶段首轮归档复核拒绝了文件数不一致：初版清单误排除了嵌套的 prior-audit/SHA256SUMS。已改为只排除新根清单自身，保留并摘要核对旧清单；未修改原始证据。最终归档复核回执单独记录清单、报告和压缩包摘要。失败原因也随包保留。

## 4. 持久化边界与后续任务可获取性

上述测试证明当前运行实例能保存和读回证据，并证明拿到 bundle 的消费者可以离线恢复固定版本。它没有证明后续全新 Cloud 任务会拿到该目录。

当前文件位于本实例 overlay。未观察到独立持久卷或平台保留合同；environment_status 返回已加载的配置版本，未返回包含本轮文件的新保存/发布凭据；当前工具未提供这些本地文件的专用持久化上传接口。本轮未进行平台快照发布、远端上传或创建新 Cloud 任务读取验证。

因此，payload 和本轮候选证据包都归为“已本地保存、跨任务持久性未证实”的任务工作文件；bundle 构建/恢复临时 bare 仓库已由 TemporaryDirectory 清理；没有把 /tmp 或目录名称当成持久存储承诺。SHA256 保证可检测的内容一致性，不证明平台保留期限或数字签名身份。

最终状态必须保持 EVIDENCE_NOT_PERSISTED。后续需要由平台支持的持久化/导出机制保存本轮目录，取得可回读的制品标识，再由新 Cloud 任务实际取回并核对清单，才可改判。此处不擅自选择 GitHub、Drive 或其他外部账户上传。

## 5. 保留的既有验收结果与未通过项目

本节整理既有对话结论和 prior-audit 原始文件，不是本轮重跑结果。

| 项目 | 保留状态 |
|---|---|
| 历史 C_F_E2E_VERIFIED、运行 42268897454 | 按用户给定的已通过冻结基线保留；本轮未查询或重跑 |
| CLOUD_STARTUP_VERIFIED | 上轮默认关闭 C 启动范围通过，17 项检查；本轮仅核对证据文件摘要 |
| C_F_CLOUD_REUSABLE_VERIFIED | 上轮新请求未通过；没有新增执行证据，本轮不改判 |
| FAILOVER_VERIFIED | 未通过；没有已准入的独立完整备用执行环境 |
| SECOND_MCP_VERIFIED | 上轮宿主原生 Firecrawl 发现与公开只读调用通过；冻结 C CLI 尚未集成 |
| 本地保存、独立进程读取、固定版本离线恢复 | 本轮 PASS |
| 正式输出路径写入 | BLOCKED：EACCES(13) |
| 跨新 Cloud 任务的证据获取和持久化 | 未通过，EVIDENCE_NOT_PERSISTED |

上轮未通过原因仍保留为历史证据：api.github.com 的 CONNECT 403；离线 C 包装清除网络/认证且未提供可写批请求状态目录；缺少真实新请求回收结果和独立备用完整路径。本轮没有重新验证这些运行条件，也不将其当作本轮网络实测。

## 6. 回滚与变更边界

本轮仅在新增审计目录内写入文件；原公开仓库前后 git status 均为空。没有修改 CodeGraph 安装、active 指针、原项目环境或系统权限，因此没有需要恢复的运行环境变更。候选文件若需撤销，只清理本轮审计目录，保留原 cf-current 证据、冻结版本和安装文件。

上轮 active 停用及恢复的真实记录保存在 prior-audit/fresh-acceptance.json，本轮没有重复演练。原先停用方式为删除 active 链接；恢复必须先校验固定版本再恢复链接，不删除审计账本，不在提交结果不确定时重提。

本轮未访问私有源码、未使用 Binance 凭据、未触发新 Actions、未部署备用执行器、未合并 main、未推送 GitHub。正式目录的权限阻断已定位，后续停止对该路径的无意义重试；持久化交付仍未完成。
