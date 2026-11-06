# C+F 工具接入协调器候选

标准库Python3.11+；真实联网只依赖已配置的gh CLI。无pip安装、服务端口、
Docker或CodeGraph重装。`tool_access.py`提供list-capabilities、preflight和invoke。
配置列表成功仅表示读出了配置。每次结果的verification_scope限定VERIFIED含义。

唯一可联网操作：固定公开仓库metadata读取，以及run42246979456的历史证据读取。
端点、GET方法、Git SHA、job、12文件blob与索引摘要固定；不接受任意URL或命令。
结果不返回源码/令牌。审计仅保存允许的tool/operation、结果码和参数摘要。

NATIVE工具必须由模型工具层调用。当前快照只是提示，不能注册工具。
C_LOCAL未安全准入，C_REMOTE未部署；协调器不会启动原CodeGraph、bwrap或远程服务。
F_BATCH历史读取成功不是新批处理，也不是实时MCP。候选没有dispatch实现。

错误码与退出码：VERIFIED0、NATIVE_TOOL_NOT_EXPOSED10、AUTH_MISSING11、
SERVER_NOT_REACHABLE12、TOOL_NOT_LISTED13、SCHEMA_MISMATCH14、
PROJECT_NOT_APPROVED15、INDEX_PROVENANCE_MISSING16、EXECUTOR_NOT_DEPLOYED17、CALL_FAILED18。

可信gh控制面保留现有代理、CA和认证注入；任何MCP子进程都不得继承该环境。
mcp_client.py仅是有界协议组件，不提供OS隔离；本轮只用明确标记的mock测试它。
原生Mock的serverInfo是mock-codegraph/0.0-test，不是CodeGraph1.6.2实测。

离线候选安装：先核对交付报告里的HASHES.json SHA256，再运行
install_candidate.py --root 一个全新workspace/tmp目录 --expected-sha256 固定摘要。
只新建版本目录或复用完全一致内容，不覆盖已有内容，不修改CODEX_HOME、启动技能、
已有安装脚本或仓库。安装成功也不等于Cloud新任务已复用。

Skill候选位于skills/tool-access-review/SKILL.md。接入环境时需要审核安装/启动说明，
保留原配置，并由所有者决定保存/发布。当前没有安装到Codex搜索路径。

未来新增仓库必须逐仓审批公开性、仓库ID、固定SHA、索引摘要与工具范围；
每仓独立执行器/数据目录。新增Context7等工具需专用schema/认证策略及实际模型
暴露或安全执行器，不能仅修改JSON便宣称任意插件可用。
