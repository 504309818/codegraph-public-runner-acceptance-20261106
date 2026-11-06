#!/usr/bin/env python3
"""Thin C entry: retain reviewed legacy routes; add only bound C->F query."""
import importlib.util,json,pathlib,sys
HERE=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'legacy'))
spec=importlib.util.spec_from_file_location('reviewed_C',HERE/'legacy/tool_access.py');legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
sys.path.insert(0,str(HERE))
import cf_batch
from cf_protocol import BatchError,PROFILES

def main(argv=None):
 argv=list(sys.argv[1:] if argv is None else argv)
 try:
  if argv==['list-capabilities']:
   result=legacy.execute('list-capabilities');result['new_batch_addon']={'binding_enabled':cf_batch.read_json(HERE/'policy/deployment.json').get('enabled'),'owner_request_grant_required':True,'scope':'FIXED_PUBLIC_TOY_PROFILES_ONLY','mode':'ON_DEMAND_ASYNC_ACTIONS_NOT_HTTP_MCP'}
  else:
   # Parse using the same bounded CLI grammar as C; do not add tool IDs,
   # credential/URL inputs, private repositories or general command execution.
   tool=argv[argv.index('--tool')+1] if '--tool' in argv else None
   op=argv[argv.index('--operation')+1] if '--operation' in argv else None
   if tool!='codegraph-batch' or (argv[0]=='invoke' and op not in PROFILES):return legacy.main(argv)
   if argv[0]=='preflight':
    if cf_batch.read_json(HERE/'policy/deployment.json').get('enabled') is not True:raise BatchError('EXECUTOR_NOT_DEPLOYED','OWNER_DEPLOYMENT_AND_REQUEST_APPROVAL_REQUIRED')
    result=cf_batch.preflight_active()
   else:
    parser=legacy.Parser();parser.add_argument('command',choices=['invoke']);parser.add_argument('--tool',required=True);parser.add_argument('--operation',required=True);parser.add_argument('--args',required=True);ns=parser.parse_args(argv)
    if len(ns.args.encode())>4096:raise BatchError('SCHEMA_MISMATCH','INPUT_LIMIT')
    args=json.loads(ns.args);legacy.arguments(legacy.configuration()[0]['tools']['codegraph-batch'],op,args)
    if cf_batch.read_json(HERE/'policy/deployment.json').get('enabled') is not True:raise BatchError('EXECUTOR_NOT_DEPLOYED','OWNER_DEPLOYMENT_NOT_APPROVED')
    result=cf_batch.invoke_active(op,args)
 except BatchError as error:result={'code':error.code,'reason':error.reason,'verification_scope':'NOT_VERIFIED'}
 except legacy.Failure as error:result={'code':error.code,'reason':error.reason,'verification_scope':'NOT_VERIFIED'}
 except Exception:result={'code':'CALL_FAILED','reason':'REDACTED_INPUT_OR_EXECUTION_ERROR','verification_scope':'NOT_VERIFIED'}
 encoded=json.dumps(result,separators=(',',':'))
 if len(encoded.encode())>32768:result={'code':'CALL_FAILED','reason':'OUTPUT_LIMIT'};encoded=json.dumps(result)
 print(encoded);return legacy.CODES.get(result.get('code'),18)

if __name__=='__main__':raise SystemExit(main())
