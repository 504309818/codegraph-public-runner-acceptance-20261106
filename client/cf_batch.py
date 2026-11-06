"""One approved C controller -> on-demand Actions -> bound F response.

Default deployment and grant are disabled. No HTTP MCP service is claimed.
workflow_dispatch is attempted at most once per durable request/owner grant.
"""
import argparse,contextlib,fcntl,json,os,pathlib,re,stat,sys,tempfile,time,urllib.parse
from cf_protocol import *
from mcp_client import BoundedProcess

HERE=pathlib.Path(__file__).resolve().parent
API_VERSION='2022-11-28'

def read_json(path):
 check(not path.is_symlink() and path.is_file() and path.stat().st_size<=1048576,'LOCAL_FILE_SCOPE')
 st=path.stat();check(st.st_nlink==1,'LOCAL_HARDLINK')
 return json.loads(path.read_text())

class Journal:
 def __init__(self,path):self.path=pathlib.Path(path)
 @contextlib.contextmanager
 def transaction(self):
  self.path.mkdir(mode=0o700,parents=True,exist_ok=True);s=self.path.lstat()
  check(stat.S_ISDIR(s.st_mode) and not self.path.is_symlink() and s.st_uid==os.getuid() and s.st_mode&0o077==0,'STATE_DIRECTORY')
  for parent in self.path.parents:check(not parent.is_symlink(),'STATE_PARENT_SYMLINK')
  fd=os.open(self.path/'lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
  try:
   st=os.fstat(fd);check(st.st_nlink==1 and stat.S_ISREG(st.st_mode) and st.st_mode&0o077==0,'STATE_LOCK')
   fcntl.flock(fd,fcntl.LOCK_EX);target=self.path/'journal.json';data=read_json(target) if target.exists() else {'requests':{},'grants':{}}
   yield data
   check(len(canonical(data))<=524288,'STATE_LIMIT')
   out,temporary=tempfile.mkstemp(prefix='.journal-',dir=self.path)
   try:
    stream=os.fdopen(out,'wb');out=None
    with stream:stream.write(canonical(data));stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,target);directory=os.open(self.path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)
   finally:
    if out is not None:os.close(out)
    if os.path.exists(temporary):os.unlink(temporary)
  finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
 def put(self,req):
  validate_request(req)
  with self.transaction() as d:
   old=d['requests'].get(req['request_id']);check(old is None or old['request']==req,'REQUEST_ID_CONFLICT','PROJECT_NOT_APPROVED')
   check(old is not None or len(d['requests'])<16,'REQUEST_COUNT_LIMIT')
   if old is None:d['requests'][req['request_id']]={'request':req,'state':'PREPARED'}
   d['active_request']=req['request_id']
  return req
 def get(self,request_id):
  check(valid_uuid(request_id),'REQUEST_ID','SCHEMA_MISMATCH')
  with self.transaction() as d:
   check(request_id in d['requests'],'REQUEST_NOT_PREPARED');return json.loads(json.dumps(d['requests'][request_id]))
 def artifact(self,request_id,kind,body):
  check(valid_uuid(request_id) and kind in {'run-log','result-frame'},'ARTIFACT_SCOPE')
  raw=body.encode() if type(body) is str else canonical(body);check(len(raw)<=1048576,'ARTIFACT_LIMIT')
  sha=__import__('hashlib').sha256(raw).hexdigest()
  with self.transaction() as d:
   folder=self.path/'artifacts';folder.mkdir(mode=0o700,exist_ok=True);check(not folder.is_symlink(),'ARTIFACT_SYMLINK')
   name=request_id+'.'+kind+'.'+sha;path=folder/name
   try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
   except FileExistsError:
    check(path.is_file() and not path.is_symlink() and path.stat().st_nlink==1 and __import__('hashlib').sha256(path.read_bytes()).hexdigest()==sha,'ARTIFACT_CONFLICT')
   else:
    try:
     with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    except Exception:raise BatchError('CALL_FAILED','ARTIFACT_WRITE')
   ref={'path':'artifacts/'+name,'sha256':sha,'bytes':len(raw)};d['requests'][request_id].setdefault('artifacts',{})[kind]=ref;return ref
 def load_frame(self,request_id,ref):
  name=request_id+'.result-frame.'+ref['sha256'];check(ref.get('path')=='artifacts/'+name and re.fullmatch('[0-9a-f]{64}',ref['sha256']) is not None,'CACHE_PATH')
  folder=self.path/'artifacts';check(not folder.is_symlink(),'CACHE_SYMLINK');path=folder/name
  value=read_json(path);check(__import__('hashlib').sha256(path.read_bytes()).hexdigest()==ref['sha256'],'CACHE_DIGEST');return value

class Github:
 def __init__(self,binding):self.b=binding;self.calls=[]
 def command(self,argv,body=None):
  env=os.environ.copy();env.pop('GH_DEBUG',None);env.update(GH_HOST='github.com',GH_PROMPT_DISABLED='1',GH_PAGER='')
  with BoundedProcess(['/usr/bin/gh',*argv],str(HERE),env,timeout=25,cap=1048576) as p:
   if body is not None:p.p.stdin.write(canonical(body));p.p.stdin.flush()
   code,out,err=p.finish()
  self.calls.append({'method':'POST' if body is not None else 'GET','exit':code})
  if code:
   match=re.search(rb'HTTP\s+(\d{3})',err);http=int(match.group(1)) if match else None
   raise BatchError('AUTH_MISSING' if http in (401,403) else 'SERVER_NOT_REACHABLE','HTTP_'+str(http) if http else 'GITHUB_TRANSPORT_UNCERTAIN')
  return out
 def api(self,path,method='GET',body=None):
  check(path.startswith('repos/'+REPOSITORY+'/') or path=='repos/'+REPOSITORY or path=='user','ENDPOINT_SCOPE','PROJECT_NOT_APPROVED')
  args=['api','--hostname','github.com','-H','X-GitHub-Api-Version: '+API_VERSION,'--method',method,path]
  if body is not None:args+=['--input','-']
  try:raw=self.command(args,body)
  except BatchError as error:error.http_method=method;raise
  return json.loads(raw) if raw.strip() else None
 def preflight(self):
  b=self.b;r=self.api('repos/'+REPOSITORY);check(r.get('id')==b['repository_id'] and r.get('full_name')==REPOSITORY and r.get('private') is False,'PUBLIC_REPOSITORY','PROJECT_NOT_APPROVED')
  identity=self.api('user');check(identity.get('id') in b['allowed_actor_ids'],'AUTHENTICATED_ACTOR','AUTH_MISSING')
  path='repos/'+REPOSITORY
  ref=self.api(path+'/git/ref/heads/'+BRANCH);check(ref['object']['sha']==b['workflow_sha'],'WORKFLOW_BRANCH_MOVED','INDEX_PROVENANCE_MISSING')
  tree=self.api(path+'/git/trees/'+b['workflow_sha']+'?recursive=1');check(not tree.get('truncated') and {x['path']:x['sha'] for x in tree.get('tree',[]) if x.get('type')=='blob'}==b['workflow_tree'],'WORKFLOW_TREE','INDEX_PROVENANCE_MISSING')
  workflow=self.api(path+'/actions/workflows/acceptance.yml');check(workflow.get('id')==b['workflow_id'] and workflow.get('state')=='active' and workflow.get('path')=='.github/workflows/acceptance.yml','WORKFLOW_IDENTITY')
  return {'authenticated_actor_id':identity['id'],'reads_verified':True,'dispatch_write_permission':'NOT_YET_TESTED'}
 def runs(self,req):
  query=urllib.parse.urlencode({'event':'workflow_dispatch','branch':BRANCH,'created':'>='+req['issued_at'],'per_page':100})
  value=self.api(f'repos/{REPOSITORY}/actions/workflows/{self.b["workflow_id"]}/runs?'+query)
  check(type(value.get('total_count')) is int and value['total_count']<=100,'RUN_LIST_TRUNCATED_FAIL_CLOSED');return value.get('workflow_runs',[])
 def submit(self,req):
  ref=self.api(f'repos/{REPOSITORY}/git/ref/heads/{BRANCH}')
  check(ref.get('object',{}).get('sha')==self.b['workflow_sha'],'BEFORE_POST_BRANCH_MOVED','INDEX_PROVENANCE_MISSING')
  return self.api(f'repos/{REPOSITORY}/actions/workflows/{self.b["workflow_id"]}/dispatches','POST',{'ref':BRANCH,'inputs':{'request_json':canonical(req).decode(),'request_id':req['request_id'],'request_sha256':req['request_sha256']}})
 def get_run(self,run_id):return self.api(f'repos/{REPOSITORY}/actions/runs/{run_id}')
 def logs(self,run_id):return self.command(['run','view',str(run_id),'--repo',REPOSITORY,'--log']).decode('utf-8')

class Bridge:
 def __init__(self,binding,grant,journal,transport,clock=None):
  self.binding=binding;self.grant=grant;self.journal=journal;self.transport=transport;self.clock=clock or (lambda:datetime.datetime.now(datetime.timezone.utc))
 def find(self,req,submitted_at):
  matches=[x for x in self.transport.runs(req) if x.get('display_title')==title(req)]
  check(len(matches)<=1,'DUPLICATE_REMOTE_RUNS_FAIL_CLOSED')
  if matches:validate_run(matches[0],self.binding,req,submitted_at)
  return matches[0] if matches else None
 def submit(self,request_id):
  row=self.journal.get(request_id);req=row['request'];approval(self.binding,self.grant,req,self.clock())
  # All concurrent C processes must share this ledger. This is not a global
  # cross-host exactly-once guarantee; Actions has no dispatch idempotency key.
  with self.journal.transaction() as d:
   row=d['requests'][request_id]
   if row['state']!='PREPARED':return dict(row,do_not_resubmit=True)
   consumed=d['grants'].get(self.grant['grant_id']);check(consumed in (None,req['request_sha256']),'OWNER_BUDGET_ALREADY_USED','PROJECT_NOT_APPROVED')
   self.transport.preflight();when=stamp(self.clock());existing=self.find(req,when)
   if existing:
    row.update(state='RUN_BOUND',run_id=existing['id'],submitted_at=when);d['grants'][self.grant['grant_id']]=req['request_sha256'];return dict(row,do_not_resubmit=True)
   row.update(state='DISPATCH_ATTEMPTED',submitted_at=when);d['grants'][self.grant['grant_id']]=req['request_sha256']
  # Durable intent committed before POST; no automatic POST retry, even if the
  # process dies, the response is lost, or GitHub accepted without a run ID.
  error=None
  returned_id=None
  try:
   response=self.transport.submit(req)
   if response is not None:
    check(type(response) is dict and type(response.get('workflow_run_id')) is int and response['workflow_run_id'] not in OLD_RUNS,'POST_RESPONSE_ID')
    returned_id=response['workflow_run_id']
    check(response.get('html_url',f'https://github.com/{REPOSITORY}/actions/runs/{returned_id}')==f'https://github.com/{REPOSITORY}/actions/runs/{returned_id}','POST_RESPONSE_URL')
   state='POST_ACCEPTED'
  except BatchError as e:
   state='DISPATCH_REJECTED' if e.reason in ['HTTP_401','HTTP_403','HTTP_404','HTTP_422'] else 'SUBMISSION_UNCERTAIN';error={'code':e.code,'reason':e.reason}
   if hasattr(e,'http_method'):error['http_method']=e.http_method
  except Exception:state='SUBMISSION_UNCERTAIN';error={'code':'CALL_FAILED','reason':'POST_OUTCOME_UNKNOWN'}
  with self.journal.transaction() as d:
   d['requests'][request_id].update(state=state,submit_error=error,post_returned_run_id=returned_id);return dict(d['requests'][request_id],do_not_resubmit=True)
 def collect(self,request_id):
  row=self.journal.get(request_id);req=row['request'];validate_request(req)
  check(row['state']!='PREPARED','NOT_SUBMITTED')
  check(self.binding.get('enabled') is True,'DEPLOYMENT_NOT_APPROVED','EXECUTOR_NOT_DEPLOYED')
  if row.get('result') is not None:
   check(type(row.get('run_id')) is int and row['run_id'] not in OLD_RUNS,'CACHE_RUN_ID')
   frame=self.journal.load_frame(request_id,row['artifacts']['result-frame']);meta=self.transport.get_run(row['run_id'])
   result=verify_result(frame,meta,self.binding,req,row['submitted_at'])
   return dict(result,raw_log=row['artifacts']['run-log'],result_frame=row['artifacts']['result-frame'],same_request_cache_reused=True,new_remote_run_created_this_call=False)
  run=self.find(req,row['submitted_at'])
  if run is None:return {'code':'CALL_FAILED','state':'PENDING_RUN_DISCOVERY','reason':row['state'],'request_id':request_id,'do_not_resubmit':True}
  if row.get('post_returned_run_id') is not None:check(run['id']==row['post_returned_run_id'],'POST_AND_DISCOVERED_RUN_ID_MISMATCH')
  with self.journal.transaction() as d:d['requests'][request_id].update(state='RUN_BOUND',run_id=run['id'])
  # Re-read the exact run, not just an entry in a list, and revalidate identity.
  run=self.transport.get_run(run['id']);validate_run(run,self.binding,req,row['submitted_at'])
  if run.get('status')!='completed':return {'code':'CALL_FAILED','state':run.get('status'),'request_id':request_id,'run_id':run['id'],'do_not_resubmit':True}
  log=self.transport.logs(run['id']);log_ref=self.journal.artifact(request_id,'run-log',log);frames=[];failures=[]
  for line in log.splitlines():
   for marker,target in [(PREFIX,frames),(FAIL_PREFIX,failures)]:
    if marker in line:
     try:target.append(json.loads(line.split(marker,1)[1]))
     except ValueError:raise BatchError('SCHEMA_MISMATCH','RESULT_JSON')
  if run.get('conclusion')!='success':
   for frame in failures:check(frame.get('request_id')==request_id and frame.get('request_sha256')==req['request_sha256'] and str(frame.get('run_id'))==str(run['id']),'FAILURE_FRAME_IDENTITY')
   with self.journal.transaction() as d:d['requests'][request_id].update(state='REMOTE_FAILED',remote_conclusion=run.get('conclusion'),failure_frames=failures)
   return {'code':'CALL_FAILED','state':'REMOTE_FAILED','request_id':request_id,'run_id':run['id'],'conclusion':run.get('conclusion'),'failure_frames':failures,'raw_log':log_ref,'do_not_resubmit':True}
  check(not failures and len(frames)==1,'RESULT_MISSING_OR_AMBIGUOUS')
  result=verify_result(frames[0],run,self.binding,req,row['submitted_at'])
  frame_ref=self.journal.artifact(request_id,'result-frame',frames[0]);result.update(raw_log=log_ref,result_frame=frame_ref)
  result['same_request_cache_reused']=False;result['new_remote_run_created_this_call']=False
  with self.journal.transaction() as d:d['requests'][request_id].update(state='VERIFIED',result=result)
  return result
 def wait(self,request_id,max_wait=1200,interval=10):
  check(0<max_wait<=1200 and 0<=interval<=30,'POLL_LIMIT','SCHEMA_MISMATCH');deadline=time.monotonic()+max_wait
  while True:
   result=self.collect(request_id)
   if result.get('code')=='VERIFIED' or result.get('state')=='REMOTE_FAILED':return result
   if time.monotonic()>=deadline:return {'code':'CALL_FAILED','state':'LOCAL_WAIT_TIMEOUT','request_id':request_id,'do_not_resubmit':True,'resume':'collect or wait with same request ID; no new POST'}
   time.sleep(min(interval,max(0,deadline-time.monotonic())))

def installed_bridge():
 binding=read_json(HERE/'policy/deployment.json');grant=read_json(HERE/'policy/approval.json')
 return Bridge(binding,grant,Journal(HERE/'_cf_state'),Github(binding))
def invoke_active(operation,args):
 bridge=installed_bridge()
 with bridge.journal.transaction() as d:ident=d.get('active_request')
 check(ident is not None,'PREPARE_REQUEST_FIRST','PROJECT_NOT_APPROVED');row=bridge.journal.get(ident)
 check(row['request']['operation']==operation and canonical(row['request']['arguments'])==canonical(args),'ACTIVE_REQUEST_MISMATCH','PROJECT_NOT_APPROVED')
 bridge.submit(ident);return bridge.wait(ident)

def preflight_active():
 bridge=installed_bridge()
 with bridge.journal.transaction() as d:ident=d.get('active_request')
 check(ident is not None,'PREPARE_REQUEST_FIRST','PROJECT_NOT_APPROVED')
 row=bridge.journal.get(ident);approval(bridge.binding,bridge.grant,row['request'],bridge.clock())
 return {'code':'VERIFIED','verification_scope':'BATCH_SUBMISSION_PREFLIGHT_ONLY_NOT_QUERY_RESULT','read_preflight':bridge.transport.preflight(),'new_remote_execution':False}

class Parser(argparse.ArgumentParser):
 def error(self,message):raise BatchError('SCHEMA_MISMATCH','CLI_SYNTAX')
def main(argv=None):
 try:
  parser=Parser();s=parser.add_subparsers(dest='command',required=True)
  p=s.add_parser('prepare');p.add_argument('--task-id',required=True);p.add_argument('--operation',choices=PROFILES,required=True)
  for name in ['plan','submit','collect','wait']:
   p=s.add_parser(name);p.add_argument('--request-id',required=True)
  args=parser.parse_args(argv);bridge=installed_bridge()
  if args.command=='prepare':result={'code':'VERIFIED','verification_scope':'REQUEST_PREPARATION_ONLY','request':bridge.journal.put(prepare(args.task_id,args.operation))}
  elif args.command=='plan':
   row=bridge.journal.get(args.request_id);result={'code':'VERIFIED','verification_scope':'PLAN_ONLY_NO_NETWORK','request':row['request'],'deployment_enabled':bridge.binding.get('enabled'),'deployment_digest':binding_digest(bridge.binding),'owner_approval_required':True}
  elif args.command=='submit':result={'code':'CALL_FAILED','verification_scope':'NOT_A_QUERY_RESULT','state':bridge.submit(args.request_id)}
  elif args.command=='collect':result=bridge.collect(args.request_id)
  else:result=bridge.wait(args.request_id)
 except BatchError as e:result={'code':e.code,'reason':e.reason,'verification_scope':'NOT_VERIFIED'}
 except Exception:result={'code':'CALL_FAILED','reason':'REDACTED_INTERNAL_ERROR','verification_scope':'NOT_VERIFIED'}
 encoded=canonical(result)
 if len(encoded)>32768:result={'code':'CALL_FAILED','reason':'CLIENT_OUTPUT_LIMIT'};encoded=canonical(result)
 print(encoded.decode());return 0 if result.get('code')=='VERIFIED' else 1

if __name__=='__main__':raise SystemExit(main())
