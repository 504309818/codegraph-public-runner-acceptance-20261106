#!/usr/bin/env python3
"""Thin F wrapper. Fetch and execute the unmodified approved F commit once."""
import hashlib,json,os,pathlib,subprocess,tarfile,tempfile,urllib.request
from cf_protocol import *
from mcp_client import BoundedProcess

HERE=pathlib.Path(__file__).resolve().parent

class FProcessError(BatchError):
 def __init__(self,reason,details):super().__init__('CALL_FAILED',reason);self.details=details

def export_fixed_F(destination):
 pins=json.loads((HERE/'F_pins.json').read_text());check(pins['sha']==F_SHA,'F_SOURCE_PIN')
 expected={x['path']:x['sha256'] for x in pins['files']}
 with urllib.request.urlopen(f'https://codeload.github.com/{REPOSITORY}/tar.gz/{F_SHA}',timeout=60) as response:data=response.read(1048577)
 check(len(data)<=1048576,'F_ARCHIVE_LIMIT')
 archive=destination.parent/'fixed-F.tar.gz';archive.write_bytes(data);seen=set()
 with tarfile.open(archive,'r:gz') as handle:
  for item in handle.getmembers():
   parts=pathlib.PurePosixPath(item.name).parts
   check(not item.name.startswith('/') and '..' not in parts and (item.isdir() or item.isfile()),'F_ARCHIVE_TYPE')
   if item.isdir():continue
   name='/'.join(parts[1:]);check(name in expected and name not in seen and item.size<=262144,'F_ARCHIVE_SCOPE')
   body=handle.extractfile(item).read();check(hashlib.sha256(body).hexdigest()==expected[name],'F_FILE_DIGEST')
   path=destination/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body);seen.add(name)
 check(seen==set(expected),'F_FILES_MISSING')
 return destination

def execute_F(source):
 env=os.environ.copy()
 for key in ['CF_REQUEST_JSON','CF_REQUEST_ID','CF_REQUEST_SHA256']:env.pop(key,None)
 # This child runs component F_SHA, not the wrapper's workflow commit. Both
 # revisions are separately recorded in the result, never conflated.
 env['GITHUB_SHA']=F_SHA
 child=BoundedProcess(['/usr/bin/python3',str(source/'controller.py')],str(source),env,cap=1048576,timeout=1100)
 try:
  with child:code,out,err=child.finish()
 except Exception as error:
  raise FProcessError('F_CONTROLLER_TIMEOUT_OR_LIMIT',{'controller_exit':child.p.returncode,'stdout':bytes(child.out).decode('utf-8',errors='replace'),'stderr':bytes(child.err).decode('utf-8',errors='replace')})
 parsed=[]
 for line in out.decode('utf-8').splitlines():
  try:row=json.loads(line)
  except ValueError:continue
  if type(row) is dict and row.get('scope')=='NEW_PUBLIC_TOY_ONLY':parsed.append(row)
 if code!=0 or len(parsed)!=1:raise FProcessError('F_CONTROLLER_EXIT',{'controller_exit':code,'stdout':out.decode('utf-8',errors='replace'),'stderr':err.decode('utf-8',errors='replace'),'controller_rows':parsed})
 return parsed[0],{'controller_exit':code,'stdout_sha256':hashlib.sha256(out).hexdigest(),'stderr_sha256':hashlib.sha256(err).hexdigest()}

def main():
 request=None
 try:
  raw=os.environ.get('CF_REQUEST_JSON','');check(len(raw.encode())<=4096,'REQUEST_SIZE')
  request=json.loads(raw);validate_request(request,date(stamp()))
  check(os.environ.get('CF_REQUEST_ID')==request['request_id'] and os.environ.get('CF_REQUEST_SHA256')==request['request_sha256'],'DISPATCH_INPUT_BINDING')
  check(os.environ.get('GITHUB_REPOSITORY')==REPOSITORY and os.environ.get('GITHUB_RUN_ATTEMPT')=='1' and os.environ.get('GITHUB_JOB')=='acceptance','EXECUTOR_CONTEXT')
  check(os.environ.get('GITHUB_WORKFLOW_REF')==REPOSITORY+'/.github/workflows/acceptance.yml@refs/heads/'+BRANCH,'WORKFLOW_REF')
  workflow_sha=os.environ.get('GITHUB_SHA','');check(re.fullmatch('[0-9a-f]{40}',workflow_sha) is not None,'WORKFLOW_SHA')
  execution={'repository':REPOSITORY,'source_sha':F_SHA,'run_id':int(os.environ['GITHUB_RUN_ID']),'run_attempt':1,'workflow_sha':workflow_sha,'actor_id':int(os.environ['GITHUB_ACTOR_ID']),'created_at':stamp()}
  with tempfile.TemporaryDirectory(prefix='cfq-fixed-source-',dir=os.environ['RUNNER_TEMP']) as temp:
   source=export_fixed_F(pathlib.Path(temp)/'source');controller,process=execute_F(source)
  execution.update(process);execution['finished_at']=stamp()
  frame=make_result(request,execution,controller)
  print(PREFIX+canonical(frame).decode());return 0
 except Exception as error:
  # Public-only context and fixed error categories. No environment/credentials.
  reason=error.reason if isinstance(error,BatchError) else type(error).__name__
  ident=request.get('request_id') if type(request) is dict else None;sha=request.get('request_sha256') if type(request) is dict else None
  print(FAIL_PREFIX+json.dumps({'protocol':'cf-failure-v1','request_id':ident if valid_uuid(ident) else None,'request_sha256':sha if type(sha) is str and re.fullmatch('[0-9a-f]{64}',sha) else None,'run_id':os.environ.get('GITHUB_RUN_ID'),'reason':reason,'details':getattr(error,'details',None)},separators=(',',':')))
  return 1

if __name__=='__main__':raise SystemExit(main())
