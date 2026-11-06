"""C/F public-only request and result contract; no network or process launch."""
import datetime,hashlib,json,re,uuid

REPOSITORY='504309818/codegraph-public-runner-acceptance-20261106'
F_SHA='c2f220778a402a706b8828d37ae8b3ad57224cf0'
ROOT='/approved/toy'
BRANCH='cf-batch-request-20261107'
PROFILES={
 'codegraph_status':{'projectPath':ROOT},
 'codegraph_search':{'projectPath':ROOT,'query':'computeInvoice','limit':10},
 'codegraph_explore':{'projectPath':ROOT,'query':'computeInvoice addNumbers','maxFiles':2}}
OLD_RUNS={42221413110,42241392916,42246979456}
PREFIX='CFQ_RESULT_V1 '
FAIL_PREFIX='CFQ_FAILURE_V1 '

class BatchError(Exception):
 def __init__(self,code,reason):super().__init__(reason);self.code=code;self.reason=reason
def check(value,reason,code='CALL_FAILED'):
 if not value:raise BatchError(code,reason)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def stamp(value=None):return (value or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc).isoformat(timespec='seconds')
def date(value):
 check(type(value) is str and len(value)<=32,'TIMESTAMP','SCHEMA_MISMATCH')
 try:result=datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
 except ValueError:raise BatchError('SCHEMA_MISMATCH','TIMESTAMP')
 check(result.tzinfo is not None and result.utcoffset().total_seconds()==0,'UTC_REQUIRED','SCHEMA_MISMATCH');return result
def valid_uuid(value):
 try:return type(value) is str and str(uuid.UUID(value,version=4))==value
 except (ValueError,AttributeError):return False
def prepare(task_id,operation,now=None):
 check(valid_uuid(task_id),'TASK_ID_UUID_REQUIRED','SCHEMA_MISMATCH');check(operation in PROFILES,'OPERATION_NOT_APPROVED','PROJECT_NOT_APPROVED')
 now=now or datetime.datetime.now(datetime.timezone.utc)
 req={'protocol':'cf-query-v1','caller_task_id':task_id,'request_id':str(uuid.uuid4()),'issued_at':stamp(now),'expires_at':stamp(now+datetime.timedelta(minutes=30)),
      'repository':REPOSITORY,'source_sha':F_SHA,'validation_suite':sorted(PROFILES),'operation':operation,'arguments':PROFILES[operation].copy()}
 req['request_sha256']=digest(req);return req
def validate_request(req,now=None):
 check(type(req) is dict and len(canonical(req))<=4096,'REQUEST_SIZE','SCHEMA_MISMATCH')
 check(set(req)=={'protocol','caller_task_id','request_id','issued_at','expires_at','repository','source_sha','validation_suite','operation','arguments','request_sha256'},'REQUEST_FIELDS','SCHEMA_MISMATCH')
 check(req['protocol']=='cf-query-v1' and valid_uuid(req['caller_task_id']) and valid_uuid(req['request_id']),'REQUEST_IDENTITY','SCHEMA_MISMATCH')
 check(req['repository']==REPOSITORY and req['source_sha']==F_SHA,'SOURCE_NOT_APPROVED','PROJECT_NOT_APPROVED')
 check(req['validation_suite']==sorted(PROFILES) and req['operation'] in PROFILES,'SUITE_NOT_APPROVED','PROJECT_NOT_APPROVED')
 check(canonical(req['arguments'])==canonical(PROFILES[req['operation']]),'MCP_ARGUMENTS_NOT_APPROVED','PROJECT_NOT_APPROVED')
 unsigned={k:v for k,v in req.items() if k!='request_sha256'};check(req['request_sha256']==digest(unsigned),'REQUEST_DIGEST','INDEX_PROVENANCE_MISSING')
 begin,end=date(req['issued_at']),date(req['expires_at']);check(0<(end-begin).total_seconds()<=1800,'REQUEST_TTL','SCHEMA_MISMATCH')
 if now is not None:check(begin-datetime.timedelta(seconds=60)<=now<=end,'REQUEST_EXPIRED','PROJECT_NOT_APPROVED')
 return req
def title(req):return 'cfq:'+req['request_id']+':'+req['request_sha256']
def binding_digest(binding):return digest({k:v for k,v in binding.items() if k!='enabled'})
def approval(binding,grant,req,now):
 validate_request(req,now)
 check(binding.get('enabled') is True,'DEPLOYMENT_NOT_APPROVED','EXECUTOR_NOT_DEPLOYED')
 check(binding.get('repository')==REPOSITORY and binding.get('source_sha')==F_SHA and binding.get('branch')==BRANCH,'DEPLOYMENT_SCOPE','PROJECT_NOT_APPROVED')
 check(type(binding.get('workflow_id')) is int and type(binding.get('repository_id')) is int and re.fullmatch('[0-9a-f]{40}',binding.get('workflow_sha','')) is not None,'DEPLOYMENT_BINDING_MISSING','EXECUTOR_NOT_DEPLOYED')
 check(binding.get('allowed_actor_ids') and all(type(x) is int for x in binding['allowed_actor_ids']),'ACTOR_BINDING_MISSING','EXECUTOR_NOT_DEPLOYED')
 check(grant.get('approved') is True and grant.get('deployment_digest')==binding_digest(binding),'OWNER_APPROVAL_REQUIRED','PROJECT_NOT_APPROVED')
 check(grant.get('caller_task_id')==req['caller_task_id'] and grant.get('request_sha256')==req['request_sha256'] and grant.get('max_submissions')==1 and grant.get('allowed_suite')==sorted(PROFILES),'GRANT_REQUEST_MISMATCH','PROJECT_NOT_APPROVED')
 check(valid_uuid(grant.get('grant_id')) and date(grant['approved_at'])<=now<=date(grant['expires_at'])<=date(req['expires_at']),'GRANT_EXPIRED','PROJECT_NOT_APPROVED')

def validate_run(meta,binding,req,submitted_at):
 check(type(meta.get('id')) is int and meta['id'] not in OLD_RUNS,'OLD_OR_INVALID_RUN','INDEX_PROVENANCE_MISSING')
 check(meta.get('repository',{}).get('id')==binding['repository_id'] and meta['repository'].get('full_name')==REPOSITORY,'RUN_REPOSITORY','INDEX_PROVENANCE_MISSING')
 check(meta.get('workflow_id')==binding['workflow_id'] and meta.get('head_sha')==binding['workflow_sha'] and meta.get('head_branch')==BRANCH and meta.get('event')=='workflow_dispatch' and meta.get('run_attempt')==1,'RUN_BINDING','INDEX_PROVENANCE_MISSING')
 check(meta.get('actor',{}).get('id') in binding['allowed_actor_ids'] and meta.get('display_title')==title(req),'RUN_ACTOR_OR_REQUEST','INDEX_PROVENANCE_MISSING')
 when=date(meta['created_at']);check(date(submitted_at)-datetime.timedelta(seconds=5)<=when<=date(req['expires_at']),'RUN_TIME','INDEX_PROVENANCE_MISSING')
 return meta['id']

def verify_controller(row,req,created_at,finished_at):
 check(row.get('status')=='PUBLIC_TOY_SECURE_RUNNER_AND_REAL_MCP_VERIFIED' and row.get('source_revision')==F_SHA,'F_EXECUTION_FAILED')
 check(row.get('cleanup')=={'errors':[],'status':'PASS'} and row.get('runner',{}).get('label')=='ubuntu-24.04','F_CLEANUP_OR_RUNNER')
 security={'environment','gateway_empty','gateway_readonly','gateway_separate','ipv6_denied','no_host_home','nonroot','private_canaries','root_readonly','runtime','source_identity','source_readonly','tcp_denied','udp_denied','unix_denied'}
 lower=(date(created_at)-datetime.timedelta(seconds=60)).timestamp()*1000;upper=(date(finished_at)+datetime.timedelta(seconds=60)).timestamp()*1000
 for phase in ['index','query']:
  p=row.get('phases',{}).get(phase,{});w=p.get('worker',{});required=security|({'index_readonly'} if phase=='query' else set())
  check(type(p.get('attach_exit')) is int and p.get('attach_exit')==0 and type(p.get('worker_exit')) is int and p.get('worker_exit')==0 and w.get('status')=='PASS' and w.get('stage')=='complete','F_PHASE_EXIT')
  check(set(w.get('safety',{}))==required and all(w['safety'][x] is True for x in required),'F_SAFETY')
  policy=p.get('controller_policy',{});expected={'readonly_root','network_none','nonroot','capabilities_dropped','private_pid_ipc','environment_allowlist','only_toy_index_bind','index_readonly'}
  check(set(policy)==expected and all(policy[x] is True for x in policy if x!='index_readonly') and policy['index_readonly'] is (phase=='query'),'F_CONTAINER_POLICY')
  check(p.get('helper_stderr')=='SECCOMP_ENFORCED\n' and w.get('source_revision')==F_SHA,'F_HELPER_OR_SOURCE')
  stats=w.get('index_stats',{});check((stats.get('fileCount'),stats.get('nodeCount'),stats.get('edgeCount'))==(2,5,6),'F_TOY_STATS')
  check(type(stats.get('lastUpdated')) in (int,float) and lower<=stats['lastUpdated']<=upper,'STALE_F_INDEX','INDEX_PROVENANCE_MISSING')
  check(re.fullmatch('[0-9a-f]{64}',w.get('index_digest','')) is not None,'F_INDEX_DIGEST')
 ix=row['phases']['index']['worker'];w=row['phases']['query']['worker']
 check(ix['index_digest']==w['index_digest'] and ix.get('index_exit')=={'code':0,'signal':None} and w.get('mcp_exit')=={'code':0,'signal':None},'F_INDEX_OR_CHILD_EXIT')
 requests=w.get('requests',[]);responses=w.get('responses',[])
 check(len(requests)==6 and len(responses)==5 and [x.get('method') for x in requests]==['initialize','notifications/initialized','tools/list','tools/call','tools/call','tools/call'],'F_RPC_SEQUENCE')
 check(all(type(x.get('id')) is int and x['id']==i and x.get('jsonrpc')=='2.0' and 'error' not in x and type(x.get('result')) is dict for i,x in enumerate(responses,1)),'F_RPC_ENVELOPE')
 init=responses[0]['result'];check(init.get('protocolVersion')=='2024-11-05' and init.get('serverInfo')=={'name':'codegraph','version':'1.6.2'},'F_MCP_IDENTITY','SCHEMA_MISMATCH')
 listed=responses[1]['result'];defs=listed.get('tools',[]);check(len(defs)==3 and sorted(x.get('name') for x in defs)==sorted(PROFILES) and not listed.get('nextCursor'),'F_TOOL_LIST','TOOL_NOT_LISTED')
 for t in defs:check(t.get('annotations',{}).get('readOnlyHint') is True and t.get('annotations',{}).get('destructiveHint') is False and 'projectPath' in t.get('inputSchema',{}).get('required',[]),'F_TOOL_SCHEMA','SCHEMA_MISMATCH')
 selected=None
 for i,name in enumerate(['codegraph_status','codegraph_search','codegraph_explore'],3):
  call=requests[i];check(call.get('id')==i and call.get('params')=={'name':name,'arguments':PROFILES[name]},'F_CALL_ARGUMENTS')
  result=responses[i-1]['result'];check(result.get('isError') is not True and result.get('content') and all(x.get('type')=='text' and type(x.get('text')) is str for x in result['content']),'F_TOOL_RESULT')
  text='\n'.join(x['text'] for x in result['content'])
  check(('**Files indexed:** 2' in text if name=='codegraph_status' else 'computeInvoice' in text if name=='codegraph_search' else all(v in text for v in ['computeInvoice','addNumbers','app.js','math.js'])),'F_OUTPUT_CONTRACT')
  if name=='codegraph_status':
   match=re.search(r'\*\*Latest file indexed:\*\*\s*(\S+)',text);check(match is not None and lower<=date(match[1]).timestamp()*1000<=upper,'STALE_STATUS_RESULT','INDEX_PROVENANCE_MISSING')
  if name==req['operation']:selected={'operation':name,'arguments':PROFILES[name],'rpc_id':i,'result':result}
 return selected

def make_result(req,execution,controller):
 validate_request(req);selected=verify_controller(controller,req,execution['created_at'],execution['finished_at'])
 frame={'protocol':'cf-result-v1','request':json.loads(canonical(req)),'execution':execution,'controller':controller,'controller_sha256':digest(controller),'selected':selected}
 frame['result_sha256']=digest(frame);check(len(canonical(frame))<=131072,'RESULT_SIZE');return frame

def verify_result(frame,meta,binding,req,submitted_at):
 validate_run(meta,binding,req,submitted_at);check(meta.get('status')=='completed' and meta.get('conclusion')=='success','REMOTE_RUN_NOT_SUCCESS')
 check(type(frame) is dict and len(canonical(frame))<=131072 and frame.get('protocol')=='cf-result-v1','RESULT_SCHEMA','SCHEMA_MISMATCH')
 check(frame.get('request')==req and frame.get('result_sha256')==digest({k:v for k,v in frame.items() if k!='result_sha256'}),'RESULT_REQUEST_OR_DIGEST','INDEX_PROVENANCE_MISSING')
 e=frame.get('execution',{});check(e.get('run_id')==meta['id'] and e.get('run_attempt')==1 and e.get('workflow_sha')==binding['workflow_sha'] and e.get('actor_id') in binding['allowed_actor_ids'] and e.get('repository')==REPOSITORY and e.get('source_sha')==F_SHA,'RESULT_EXECUTION_IDENTITY','INDEX_PROVENANCE_MISSING')
 check(type(e.get('controller_exit')) is int and e['controller_exit']==0,'RESULT_CONTROLLER_EXIT')
 check(date(meta['created_at'])<=date(e['created_at'])<=date(e['finished_at'])<=date(req['expires_at']),'RESULT_EXECUTION_TIME','INDEX_PROVENANCE_MISSING')
 controller=frame.get('controller',{});check(frame.get('controller_sha256')==digest(controller),'CONTROLLER_DIGEST','INDEX_PROVENANCE_MISSING')
 selected=verify_controller(controller,req,meta['created_at'],e['finished_at']);check(frame.get('selected')==selected,'SELECTED_RESULT_MISMATCH')
 return {'code':'VERIFIED','verification_scope':'F_BATCH_FRESH_REQUEST_RESULT','is_historical_replay':False,'caller_task_id':req['caller_task_id'],'request_id':req['request_id'],'request_sha256':req['request_sha256'],'request_issued_at':req['issued_at'],'executed_at':e['finished_at'],'run_id':meta['id'],'run_attempt':1,'run_url':f'https://github.com/{REPOSITORY}/actions/runs/{meta["id"]}','workflow_sha':binding['workflow_sha'],'source_sha':F_SHA,'index_digest':controller['phases']['query']['worker']['index_digest'],'selected':selected,'result_sha256':frame['result_sha256']}
