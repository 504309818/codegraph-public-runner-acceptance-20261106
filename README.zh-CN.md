# CodeGraph C→F公开测试客户端交付

这是默认关闭的标准库Python C客户端公开源码，与F执行分支分开存放，
避免改变已冻结的F执行提交及16文件tree。此资料分支没有workflow，不能执行Actions。
不向原量化环境安装，不启用C_LOCAL，不提供HTTPS常驻MCP。

## 固定身份

- 仓库：504309818/codegraph-public-runner-acceptance-20261106。
- F执行分支：cf-batch-request-20261107。
- F封装提交：b94866f8a57311ec72d34c432ea3fccf70862fa0。
- 实际F组件提交：c2f220778a402a706b8828d37ae8b3ad57224cf0。
- CLIENT_HASHES.json SHA256：5dd8975db10dae1c0119189f02f42ee4d3e45fa960fb9cc6ddeace5f0c72a67a。
- client/内15文件逐一匹配该manifest；legacy保持原协调器字节。

## 操作边界

本次仅交付，未创建新Actions运行。37项批处理离线测试通过；其中模拟F后端
不代表真实CodeGraph新调用。历史F成功run42246979456不得用作新请求结果。
部署配置enabled=false、批准模板approved=false；代码/模板不是GitHub凭据或所有者批准。
legacy中的README、Skill及install_candidate.py仅描述旧历史路由，不用于安装新客户端；
新任务应使用client/tool_access.py以及独立的受批准版本部署说明。

准备请求无需网络：

```text
python client/cf_batch.py prepare --task-id <本次关联UUID> --operation codegraph_search
python client/cf_batch.py plan --request-id <刚返回的request_id>
```

不要使用预先写死的示例请求；请求有效期最多30分钟。正式授权后才生成本次UUID与摘要、
绑定真实Cloud任务ID、开启精确部署门禁并写入一次额度非秘密收据。
需要单一公开仓库的Actions读取/写入身份；不在源码、收据、日志里写入token。
当前交付批准不授权dispatch，也不授权修改Cloud环境。

## 未来一次闭环验收

单一C控制器/共享持久journal，只提交一个新请求，固定search参数：
projectPath=/approved/toy, query=computeInvoice, limit=10。
为保持旧F原样，一个job仍执行initialize/tools/list及status/search/explore全部三工具，
新建且仅新建公开toy索引，再返回所选search原始响应。
ubuntu-24.04标准执行器，20分钟上限，permissions={}、容器隔离不变。
预计公开标准runner成本US$0，以GitHub当时政策/账单为准；不购买服务或存储。
失败/未知提交结果均禁止自动POST重试；只能GET同请求状态/日志。

成功必须具有全新run链接、attempt1、固定wrapper/F SHA、匹配任务/request_id/摘要、
新索引时间与MCP原始响应、只读一致性、子进程退出0和清理PASS。
只读预检或历史日志通过不是新请求成功。

## 仍需解决的边界

- 实际dispatch写权限、分支输入schema和结果回收均需一次获批准真实运行验证。
- Actions ref查询与POST并非原子SHA提交；所有者需冻结批准分支，发现移动即停止。
- 仅同共享journal提供单次提交保证；多个独立状态目录不得复用同一收据。
- SHA256是完整性，不是签名；依赖受信C、认证GitHub API和冻结workflow。
- 全新Cloud任务复用未验证，原Cloud环境不变；本分支发布不等于环境部署。
