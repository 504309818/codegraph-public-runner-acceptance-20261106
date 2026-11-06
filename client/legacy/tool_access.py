#!/usr/bin/env python3
"""Closed-by-default C+F coordinator, standard library only.

Live operations: GET the one approved public repository and historical run.
No Actions dispatch, local CodeGraph launch, arbitrary URL, native-tool emulation
or remote executor is implemented. Configuration/snapshot data is not proof.
"""
import argparse,datetime,hashlib,json,os,pathlib,re,shutil,stat,sys
from mcp_client import BoundedProcess,CODES,Failure,require,retry_readonly

BASE=pathlib.Path(__file__).resolve().parent
REPO='504309818/codegraph-public-runner-acceptance-20261106'
SHA='c2f220778a402a706b8828d37ae8b3ad57224cf0'
RUN=42246979456
JOB=127991653085
ROOT='/approved/toy'
NAMES=['codegraph_explore','codegraph_search','codegraph_status']
NATIVE_NAMES={'github-native':None,'context7-native':None,'codegraph-native':None,
 'cloud-environment-native':'mcp__codex_apps__cloud_environment_environment_status'}
TOOL_ROUTES={'github-native':'NATIVE','context7-native':'NATIVE','codegraph-native':'NATIVE',
 'cloud-environment-native':'NATIVE','codegraph-local':'C_LOCAL','codegraph-remote':'C_REMOTE',
 'github-history':'F_BATCH','codegraph-batch':'F_BATCH'}

def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def read_json(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<=65536,'SCHEMA_MISMATCH','CONFIG_FILE')
    try:return json.loads(path.read_text())
    except (ValueError,UnicodeError):raise Failure('SCHEMA_MISMATCH','CONFIG_JSON')

def configuration():
    registry=read_json(BASE/'registry.json');pins=read_json(BASE/'pins.json');snapshot=read_json(BASE/'native_snapshot.json')
    require(registry.get('schema_version')==1 and set(registry.get('tools',{}))==set(TOOL_ROUTES),'SCHEMA_MISMATCH','REGISTRY_IDS')
    require(registry.get('new_actions_authorized') is False,'PROJECT_NOT_APPROVED','ACTIONS_DISABLED')
    for key,route in TOOL_ROUTES.items():
        require(registry['tools'][key]['route']==route and registry['tools'][key]['read_only'] is True,'SCHEMA_MISMATCH','REGISTRY_ROUTE')
    require(pins.get('repository')==REPO and pins.get('sha')==SHA and pins.get('run_id')==RUN and pins.get('job_id')==JOB and pins.get('approved_project')==ROOT,'INDEX_PROVENANCE_MISSING','PIN_IDENTITY')
    require(re.fullmatch('[0-9a-f]{64}',pins.get('index_digest','')) is not None and len(pins.get('files',{}))==12,'INDEX_PROVENANCE_MISSING','PIN_DIGEST')
    return registry,pins,snapshot

def arguments(tool,operation,args):
    require(type(args) is dict,'SCHEMA_MISMATCH','ARGS_OBJECT')
    require(operation in tool['allowed_operations'],'PROJECT_NOT_APPROVED','OPERATION_NOT_APPROVED')
    def walk(value,depth=0):
        require(depth<=12,'SCHEMA_MISMATCH','ARGS_DEPTH')
        if isinstance(value,dict):
            for key,item in value.items():
                require(type(key) is str and key.lower() not in {'url','endpoint','command','token','secret','password','credential','env'},'PROJECT_NOT_APPROVED','SENSITIVE_OR_DYNAMIC_INPUT')
                walk(item,depth+1)
        elif isinstance(value,list):
            require(len(value)<=32,'SCHEMA_MISMATCH','ARGS_ARRAY_LIMIT')
            for item in value:walk(item,depth+1)
        elif isinstance(value,str):
            require(len(value)<=512 and not any(ord(c)<32 for c in value),'SCHEMA_MISMATCH','ARGS_STRING_LIMIT')
            require('://' not in value and '\\' not in value and '..' not in value,'PROJECT_NOT_APPROVED','URL_OR_PATH_ESCAPE')
    walk(args)
    if operation in NAMES:
        required={'projectPath'}|({'query'} if operation!='codegraph_status' else set())
        allowed=required|({'limit'} if operation=='codegraph_search' else {'maxFiles'} if operation=='codegraph_explore' else set())
        require(set(args)<=allowed and required<=set(args),'SCHEMA_MISMATCH','OPERATION_ARGUMENTS')
        require(args['projectPath']==ROOT,'PROJECT_NOT_APPROVED','ROOT_NOT_APPROVED')
        if 'query' in args:require(type(args['query']) is str and re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$ .:-]{0,255}',args['query']) is not None,'PROJECT_NOT_APPROVED','QUERY_SCOPE')
        for key,maximum in [('limit',10),('maxFiles',2)]:
            if key in args:require(type(args[key]) is int and 1<=args[key]<=maximum,'SCHEMA_MISMATCH','QUERY_BOUND')
    elif operation in ('read_repository','read_verified_run'):
        expected={'repository'} if operation=='read_repository' else {'repository','sha','run_id'}
        require(set(args)==expected,'SCHEMA_MISMATCH','OPERATION_ARGUMENTS')
        require(args['repository']==REPO,'PROJECT_NOT_APPROVED','REPOSITORY_NOT_APPROVED')
        if operation=='read_verified_run':
            require(type(args['run_id']) is int and args['run_id']==RUN and args['sha']==SHA,'INDEX_PROVENANCE_MISSING','RUN_SHA_NOT_APPROVED')
    else:require(not args,'SCHEMA_MISMATCH','NATIVE_ARGS_NOT_IMPLEMENTED')

class GitHubReadOnly:
    def __init__(self):self.records=[]
    def command(self,argv,label,cap=524288):
        exe=shutil.which('gh');require(exe=='/usr/bin/gh','AUTH_MISSING','APPROVED_GITHUB_CLI_UNAVAILABLE')
        # Trusted control-plane CLI retains injected authentication/proxy/CA
        # variables. This environment is never passed to an MCP subprocess.
        env=os.environ.copy();env.pop('GH_DEBUG',None);env['GH_HOST']='github.com';env['GH_PROMPT_DISABLED']='1';env['GH_PAGER']=''
        def once():
            with BoundedProcess([exe,*argv],str(BASE),env,cap=cap,timeout=25) as child:code,out,err=child.finish()
            self.records.append({'endpoint_id':label,'exit':code,'output_bytes':len(out)})
            if code:
                match=re.search(rb'HTTP\s+(\d{3})',err);http=int(match.group(1)) if match else None
                if http in (401,403):raise Failure('AUTH_MISSING','GITHUB_READ_AUTHORIZATION_DENIED')
                if http in (429,502,503,504):raise Failure('SERVER_NOT_REACHABLE','GITHUB_TRANSIENT',True)
                raise Failure('SERVER_NOT_REACHABLE','GITHUB_GET_FAILED')
            return out
        return retry_readonly(once)
    def get(self,kind):
        endpoints={'repository':f'repos/{REPO}','run':f'repos/{REPO}/actions/runs/{RUN}',
         'tree':f'repos/{REPO}/git/trees/{SHA}?recursive=1','jobs':f'repos/{REPO}/actions/runs/{RUN}/jobs'}
        require(kind in endpoints,'PROJECT_NOT_APPROVED','ENDPOINT_NOT_APPROVED')
        raw=self.command(['api','--hostname','github.com','--method','GET',endpoints[kind]],kind)
        try:return json.loads(raw)
        except ValueError:raise Failure('SCHEMA_MISMATCH','GITHUB_JSON')
    def log(self):
        return self.command(['run','view',str(RUN),'--repo',REPO,'--log'],'fixed_run_log').decode('utf-8')

def repository_check(meta):
    require(meta.get('full_name')==REPO and meta.get('owner',{}).get('login')=='504309818' and meta.get('visibility')=='public' and meta.get('private') is False,'PROJECT_NOT_APPROVED','PUBLIC_REPOSITORY_IDENTITY')
    return {'repository':REPO,'visibility':'public','metadata_read':True,'write_scope_tested':False}

def verify_historical(meta,tree,jobs,log,pins):
    require(meta.get('id')==RUN and meta.get('head_sha')==SHA and meta.get('event')=='workflow_dispatch' and meta.get('run_attempt')==1 and meta.get('status')=='completed' and meta.get('conclusion')=='success','INDEX_PROVENANCE_MISSING','HISTORICAL_RUN_IDENTITY')
    require(meta.get('head_branch')=='fix/mcp-gateway-20261106' and meta.get('path')=='.github/workflows/acceptance.yml','INDEX_PROVENANCE_MISSING','HISTORICAL_WORKFLOW')
    require(tree.get('truncated') is False and {x['path']:x['sha'] for x in tree.get('tree',[]) if x.get('type')=='blob'}==pins['files'],'INDEX_PROVENANCE_MISSING','SOURCE_TREE')
    expected=[x for x in jobs.get('jobs',[]) if x.get('id')==JOB]
    require(len(expected)==1 and expected[0].get('conclusion')=='success' and 'ubuntu-24.04' in expected[0].get('labels',[]),'CALL_FAILED','HISTORICAL_JOB')
    rows=[]
    for line in log.splitlines():
        pos=line.find('{"')
        if pos>=0:
            try:value=json.loads(line[pos:])
            except ValueError:continue
            if value.get('scope')=='NEW_PUBLIC_TOY_ONLY':rows.append(value)
    require(len(rows)==1,'CALL_FAILED','HISTORICAL_CONTROLLER_MISSING')
    r=rows[0];require(r.get('status')=='PUBLIC_TOY_SECURE_RUNNER_AND_REAL_MCP_VERIFIED' and r.get('source_revision')==SHA,'CALL_FAILED','HISTORICAL_CONTROLLER_STATUS')
    require(r.get('runner',{}).get('label')=='ubuntu-24.04' and re.fullmatch('sha256:[0-9a-f]{64}',r.get('worker_image','')),'CALL_FAILED','HISTORICAL_RUNTIME')
    require(r.get('cleanup')=={'errors':[],'status':'PASS'},'CALL_FAILED','HISTORICAL_CLEANUP')
    security={'environment','gateway_empty','gateway_readonly','gateway_separate','ipv6_denied','no_host_home','nonroot','private_canaries','root_readonly','runtime','source_identity','source_readonly','tcp_denied','udp_denied','unix_denied'}
    for phase in ('index','query'):
        item=r.get('phases',{}).get(phase,{});worker=item.get('worker',{})
        require(item.get('attach_exit')==0 and item.get('worker_exit')==0 and worker.get('status')=='PASS' and worker.get('stage')=='complete','CALL_FAILED','HISTORICAL_PHASE_EXIT')
        required=security|({'index_readonly'} if phase=='query' else set())
        require(set(worker.get('safety',{}))==required and all(worker['safety'][k] is True for k in required),'CALL_FAILED','HISTORICAL_SAFETY')
        policy=item.get('controller_policy',{})
        require(set(policy)=={'readonly_root','network_none','nonroot','capabilities_dropped','private_pid_ipc','environment_allowlist','only_toy_index_bind','index_readonly'} and all(policy[k] is True for k in policy if k!='index_readonly') and policy['index_readonly'] is (phase=='query'),'CALL_FAILED','HISTORICAL_CONTAINER_POLICY')
        require(item.get('helper_stderr')=='SECCOMP_ENFORCED\n','CALL_FAILED','HISTORICAL_SECCOMP')
        require(worker.get('source_revision')==SHA and worker.get('index_digest')==pins['index_digest'],'INDEX_PROVENANCE_MISSING','HISTORICAL_INDEX_DIGEST')
        stats=worker.get('index_stats',{})
        require((stats.get('fileCount'),stats.get('nodeCount'),stats.get('edgeCount'))==(2,5,6),'INDEX_PROVENANCE_MISSING','HISTORICAL_TOY_STATS')
    ix=r['phases']['index']['worker'];w=r['phases']['query']['worker']
    require(ix.get('index_exit')=={'code':0,'signal':None} and w.get('mcp_exit')=={'code':0,'signal':None},'CALL_FAILED','HISTORICAL_CHILD_EXIT')
    req=w.get('requests',[]);res=w.get('responses',[])
    require(len(req)==6 and len(res)==5 and [x.get('method') for x in req]==['initialize','notifications/initialized','tools/list','tools/call','tools/call','tools/call'],'CALL_FAILED','HISTORICAL_RPC_SEQUENCE')
    require([x.get('id') for x in res]==[1,2,3,4,5] and all(type(x.get('id')) is int and x.get('jsonrpc')=='2.0' and 'error' not in x and isinstance(x.get('result'),dict) for x in res),'CALL_FAILED','HISTORICAL_RPC_ENVELOPE')
    init=res[0]['result'];require(init.get('protocolVersion')=='2024-11-05' and init.get('serverInfo')=={'name':'codegraph','version':'1.6.2'},'SCHEMA_MISMATCH','HISTORICAL_MCP_IDENTITY')
    listed=res[1]['result'];defs=listed.get('tools',[])
    require(len(defs)==3 and sorted(t.get('name') for t in defs)==NAMES and not listed.get('nextCursor'),'TOOL_NOT_LISTED','HISTORICAL_TOOL_LIST')
    for t in defs:
        require(t.get('annotations',{}).get('readOnlyHint') is True and t.get('annotations',{}).get('destructiveHint') is False and 'projectPath' in t.get('inputSchema',{}).get('required',[]),'SCHEMA_MISMATCH','HISTORICAL_TOOL_SCHEMA')
    outputs={};expected_names=['codegraph_status','codegraph_search','codegraph_explore']
    for n,name in enumerate(expected_names,3):
        call=req[n];require(call.get('id')==n and call.get('jsonrpc')=='2.0' and call.get('params',{}).get('name')==name,'CALL_FAILED','HISTORICAL_TOOL_ID')
        arguments({'allowed_operations':NAMES},name,call['params'].get('arguments',{}))
        value=res[n-1]['result'];require(value.get('isError') is not True and isinstance(value.get('content'),list) and value['content'],'CALL_FAILED','HISTORICAL_TOOL_ERROR')
        require(all(x.get('type')=='text' and type(x.get('text')) is str for x in value['content']),'SCHEMA_MISMATCH','HISTORICAL_TOOL_CONTENT')
        outputs[name]='\n'.join(x['text'] for x in value['content'])
    require(re.search(r'\*\*Files indexed:\*\*\s*2\b',outputs['codegraph_status']) is not None and 'computeInvoice' in outputs['codegraph_search'] and all(x in outputs['codegraph_explore'] for x in ['computeInvoice','addNumbers','app.js','math.js']),'CALL_FAILED','HISTORICAL_TOY_OUTPUT')
    return {'evidence_kind':'HISTORICAL_GITHUB_ACTIONS','is_new_execution':False,'repository':REPO,'source_sha':SHA,'run_id':RUN,'run_url':f'https://github.com/{REPO}/actions/runs/{RUN}',
      'index_digest':pins['index_digest'],'files':2,'nodes':5,'edges':6,'server_identity':'codegraph 1.6.2','historical_tools_list':NAMES,
      'historical_call_verified':{name:True for name in expected_names},'mcp_exit':0,'cleanup':'PASS',
      'status_freshness':'UNKNOWN_PRESERVED','journal':'DELETE_INTENTIONAL_TOY_ONLY','live_codegraph_call_verified':False}

def history(client,pins,only_repository=False):
    meta=client.get('repository');public=repository_check(meta)
    if only_repository:return public
    return verify_historical(client.get('run'),client.get('tree'),client.get('jobs'),client.log(),pins)

def audit(result,args):
    folder=BASE/'_state';folder.mkdir(mode=0o700,exist_ok=True)
    st=folder.lstat();require(stat.S_ISDIR(st.st_mode) and not folder.is_symlink() and st.st_uid==os.getuid() and st.st_mode & 0o077==0,'CALL_FAILED','AUDIT_DIRECTORY')
    path=folder/'audit.jsonl'
    record={'time':utc(),'tool_id':result.get('tool_id'),'route':result.get('route'),'operation':result.get('operation'),'code':result['code'],'verification_scope':result.get('verification_scope'),'args_sha256':hashlib.sha256(json.dumps(args,sort_keys=True).encode()).hexdigest()}
    fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd);require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1 and info.st_mode & 0o077==0,'CALL_FAILED','AUDIT_FILE')
        os.write(fd,(json.dumps(record,separators=(',',':'))+'\n').encode())
    finally:os.close(fd)

def execute(command,tool_id=None,operation=None,args=None,client_factory=GitHubReadOnly):
    registry,pins,snapshot=configuration();args=args or {}
    if command=='list-capabilities':
        return {'code':'VERIFIED','verification_scope':'CONFIGURATION_LIST_ONLY','tools':list(registry['tools'].values()),'native_snapshot_is_current_proof':False,'new_actions_authorized':False}
    require(tool_id in registry['tools'],'SCHEMA_MISMATCH','TOOL_ID_UNKNOWN');tool=registry['tools'][tool_id];route=tool['route']
    result={'schema_version':1,'observed_at':utc(),'tool_id':tool_id,'route':route,'operation':operation or 'preflight','checks':{'configuration':'PRESENT','live_mcp_handshake':'NOT_VERIFIED','live_tools_list':'NOT_VERIFIED','live_status':'NOT_VERIFIED','live_search':'NOT_VERIFIED','live_explore':'NOT_VERIFIED'}}
    if command=='invoke':arguments(tool,operation,args)
    if route=='NATIVE':
        name=NATIVE_NAMES[tool_id];hint=name is not None and name in snapshot['names']
        result.update(code='CALL_FAILED' if hint else 'NATIVE_TOOL_NOT_EXPOSED',verification_scope='NOT_VERIFIED',reason='NATIVE_MODEL_DISPATCH_REQUIRED' if hint else 'MODEL_TOOL_NOT_IN_OBSERVED_CATALOG',native_tool_name=name)
        result.update(native_snapshot_hint_exposed=hint,native_snapshot_observed_at=snapshot.get('observed_at_utc'),native_current_exposure='NOT_VERIFIED_BY_COMMAND_PROCESS')
    elif route=='C_LOCAL':result.update(code='PROJECT_NOT_APPROVED',verification_scope='NOT_VERIFIED',reason='LOCAL_EXECUTOR_NOT_SAFELY_ADMITTED')
    elif route=='C_REMOTE':result.update(code='EXECUTOR_NOT_DEPLOYED',verification_scope='NOT_VERIFIED',reason='NO_APPROVED_AUTHENTICATED_HTTPS_EXECUTOR')
    elif tool_id=='codegraph-batch' and command=='invoke' and operation in NAMES:
        result.update(code='EXECUTOR_NOT_DEPLOYED',verification_scope='NOT_VERIFIED',reason='NEW_BATCH_NOT_AUTHORIZED_HISTORICAL_RESULTS_ARE_NOT_LIVE_CALLS')
    else:
        client=client_factory();data=history(client,pins,command=='invoke' and operation=='read_repository')
        result.update(code='VERIFIED',verification_scope='GITHUB_PUBLIC_METADATA_READ' if operation=='read_repository' else 'HISTORICAL_F_BATCH_EVIDENCE_READ',data=data,transport='GitHub REST/API via trusted gh CLI, not MCP',transport_observations=client.records)
        result['checks'].update(authorization='VERIFIED_FOR_THIS_PUBLIC_READ',reachability='VERIFIED',mcp_protocol_on_this_route='NOT_APPLICABLE_REST',live_codegraph_call='NOT_PERFORMED')
    return result

class Parser(argparse.ArgumentParser):
    def error(self,message):raise Failure('SCHEMA_MISMATCH','CLI_SYNTAX')

def main(argv=None):
    result={};args={};safe_tool=None;safe_operation=None
    try:
        parser=Parser();sub=parser.add_subparsers(dest='command',required=True)
        sub.add_parser('list-capabilities')
        pre=sub.add_parser('preflight');pre.add_argument('--tool',required=True)
        call=sub.add_parser('invoke');call.add_argument('--tool',required=True);call.add_argument('--operation',required=True);call.add_argument('--args',required=True)
        ns=parser.parse_args(argv);tool=getattr(ns,'tool',None);op=getattr(ns,'operation',None)
        safe_tool=tool if tool in TOOL_ROUTES else None
        if safe_tool:
            registry,_,_=configuration();safe_operation=op if op in registry['tools'][safe_tool]['allowed_operations'] else None
        raw=getattr(ns,'args','{}');require(len(raw.encode())<=4096,'SCHEMA_MISMATCH','INPUT_LIMIT')
        try:args=json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        except (ValueError,RecursionError):raise Failure('SCHEMA_MISMATCH','ARGS_JSON')
        require(type(args) is dict,'SCHEMA_MISMATCH','ARGS_OBJECT')
        result=execute(ns.command,tool,op,args)
    except Failure as error:result={'code':error.code,'reason':error.reason,'tool_id':safe_tool,'operation':safe_operation,'verification_scope':'NOT_VERIFIED'}
    except Exception:result={'code':'CALL_FAILED','reason':'INTERNAL_FAILURE_REDACTED','tool_id':safe_tool,'operation':safe_operation,'verification_scope':'NOT_VERIFIED'}
    try:audit(result,args)
    except Exception:result={'code':'CALL_FAILED','reason':'AUDIT_FAILURE_REDACTED','verification_scope':'NOT_VERIFIED'}
    encoded=json.dumps(result,ensure_ascii=False,separators=(',',':'))
    if len(encoded.encode())>32768:result={'code':'CALL_FAILED','reason':'OUTPUT_LIMIT','verification_scope':'NOT_VERIFIED'};encoded=json.dumps(result)
    print(encoded);return CODES[result['code']]

if __name__=='__main__':raise SystemExit(main())
