"""Bounded stdio MCP protocol component. No production executor is admitted.

Tests use an explicitly named mock server. This module supplies no filesystem
or network sandbox and must not be used to admit the legacy CodeGraph launcher.
"""
import json,math,os,selectors,signal,subprocess,time

CODES={'VERIFIED':0,'NATIVE_TOOL_NOT_EXPOSED':10,'AUTH_MISSING':11,
       'SERVER_NOT_REACHABLE':12,'TOOL_NOT_LISTED':13,'SCHEMA_MISMATCH':14,
       'PROJECT_NOT_APPROVED':15,'INDEX_PROVENANCE_MISSING':16,
       'EXECUTOR_NOT_DEPLOYED':17,'CALL_FAILED':18}

class Failure(Exception):
    def __init__(self,code,reason,retryable=False):
        super().__init__(code);self.code=code;self.reason=reason;self.retryable=retryable

def require(ok,code,reason):
    if not ok:raise Failure(code,reason)

def validate_args(args,schema):
    require(isinstance(args,dict) and schema.get('type')=='object','SCHEMA_MISMATCH','OBJECT_REQUIRED')
    props=schema.get('properties',{})
    require(isinstance(props,dict),'SCHEMA_MISMATCH','PROPERTIES_REQUIRED')
    require(set(schema.get('required',[]))<=set(args),'SCHEMA_MISMATCH','MISSING_REQUIRED_ARGUMENT')
    require(set(args)<=set(props),'SCHEMA_MISMATCH','UNKNOWN_ARGUMENT')
    for key,value in args.items():
        spec=props[key];kind=spec.get('type')
        good=(kind=='string' and type(value) is str) or (kind=='number' and type(value) in (int,float)) or (kind=='integer' and type(value) is int) or (kind=='boolean' and type(value) is bool)
        require(good,'SCHEMA_MISMATCH','ARGUMENT_TYPE')
        if kind in ('number','integer'):require(math.isfinite(value),'SCHEMA_MISMATCH','ARGUMENT_FINITE')
        if kind=='string':require(len(value)<=spec.get('maxLength',512),'SCHEMA_MISMATCH','STRING_TOO_LONG')
        if 'enum' in spec:require(value in spec['enum'],'SCHEMA_MISMATCH','ARGUMENT_ENUM')
        if 'minimum' in spec:require(value>=spec['minimum'],'SCHEMA_MISMATCH','ARGUMENT_RANGE')
        if 'maximum' in spec:require(value<=spec['maximum'],'SCHEMA_MISMATCH','ARGUMENT_RANGE')

def retry_readonly(fn,max_attempts=2):
    require(type(max_attempts) is int and 1<=max_attempts<=2,'SCHEMA_MISMATCH','RETRY_LIMIT')
    for attempt in range(max_attempts):
        try:return fn()
        except Failure as error:
            if not error.retryable or attempt+1==max_attempts:raise
            time.sleep(0.01)

class BoundedProcess:
    def __init__(self,argv,cwd,env,cap=524288,timeout=20):
        self.p=subprocess.Popen(argv,cwd=cwd,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
        self.selector=selectors.DefaultSelector();self.selector.register(self.p.stdout,selectors.EVENT_READ,'out');self.selector.register(self.p.stderr,selectors.EVENT_READ,'err')
        for fd in [self.p.stdout.fileno(),self.p.stderr.fileno()]:os.set_blocking(fd,False)
        self.out=bytearray();self.err=bytearray();self.total_out=0;self.total_err=0;self.cap=cap;self.deadline=time.monotonic()+timeout;self.exit=None;self.closed=False
    def pump(self,deadline=None):
        limit=min(self.deadline,deadline) if deadline is not None else self.deadline
        require(time.monotonic()<limit,'SERVER_NOT_REACHABLE','PROCESS_TIMEOUT')
        for key,_ in self.selector.select(min(0.05,max(0,limit-time.monotonic()))):
            data=os.read(key.fileobj.fileno(),65536)
            if not data:self.selector.unregister(key.fileobj);continue
            target=self.out if key.data=='out' else self.err;target.extend(data)
            if key.data=='out':self.total_out+=len(data)
            else:self.total_err+=len(data)
            require(self.total_out<=self.cap and self.total_err<=65536,'CALL_FAILED','PROCESS_OUTPUT_LIMIT')
    def finish(self):
        if self.p.stdin and not self.p.stdin.closed:self.p.stdin.close()
        while self.selector.get_map():self.pump()
        remaining=max(0.001,self.deadline-time.monotonic())
        try:self.exit=self.p.wait(timeout=remaining)
        except subprocess.TimeoutExpired:raise Failure('SERVER_NOT_REACHABLE','PROCESS_TIMEOUT')
        return self.exit,bytes(self.out),bytes(self.err)
    def close(self):
        # Kill the entire temporary process group, including descendants which
        # may outlive an exited parent. Never target the caller's process group.
        if self.closed:return
        self.closed=True
        try:os.killpg(self.p.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:self.p.wait(timeout=0.2)
        except subprocess.TimeoutExpired:pass
        try:os.killpg(self.p.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        self.p.wait(timeout=1)
        self.selector.close()
        for stream in [self.p.stdin,self.p.stdout,self.p.stderr]:
            if stream and not stream.closed:stream.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()

class StdioClient:
    """Internal protocol client; command admission belongs to the caller.

    Production coordinator never constructs this for C_LOCAL/C_REMOTE. Unit
    tests use a clean-env mock with serverInfo.name='mock-codegraph'.
    """
    def __init__(self,argv,cwd,env,timeout=3):
        self.process=BoundedProcess(argv,cwd,env,timeout=timeout);self.messages=[];self.ident=0
    def send(self,message):
        encoded=(json.dumps(message,separators=(',',':'))+'\n').encode()
        require(len(encoded)<=8192,'SCHEMA_MISMATCH','INPUT_LIMIT')
        try:self.process.p.stdin.write(encoded);self.process.p.stdin.flush()
        except (BrokenPipeError,OSError):raise Failure('CALL_FAILED','MCP_STDIN_CLOSED')
        self.messages.append({'direction':'request','message':message})
    def rpc(self,method,params):
        self.ident+=1;ident=self.ident;self.send({'jsonrpc':'2.0','id':ident,'method':method,'params':params})
        while True:
            if b'\n' in self.process.out:
                line,rest=self.process.out.split(b'\n',1);self.process.out=bytearray(rest)
                try:message=json.loads(line)
                except (ValueError,UnicodeError):raise Failure('CALL_FAILED','MCP_INVALID_JSON')
                require(isinstance(message,dict) and message.get('jsonrpc')=='2.0' and type(message.get('id')) is int and message.get('id')==ident,'CALL_FAILED','MCP_UNEXPECTED_ID')
                require('error' not in message and isinstance(message.get('result'),dict),'CALL_FAILED','MCP_ERROR_ENVELOPE')
                self.messages.append({'direction':'response','message':message});return message['result']
            require(self.process.p.poll() is None or self.process.selector.get_map(),'CALL_FAILED','MCP_TRANSPORT_CLOSED')
            self.process.pump()
    def discover(self,server_name,version,allowed):
        init=self.rpc('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'cf-access-mock-tests','version':'1'}})
        require(init.get('protocolVersion')=='2024-11-05' and init.get('serverInfo')=={'name':server_name,'version':version},'SCHEMA_MISMATCH','SERVER_IDENTITY')
        self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
        listed=self.rpc('tools/list',{});defs=listed.get('tools')
        require(isinstance(defs,list) and all(isinstance(t,dict) for t in defs) and not listed.get('nextCursor'),'TOOL_NOT_LISTED','INVALID_TOOLS_LIST')
        require({t.get('name') for t in defs}==set(allowed) and len(defs)==len(allowed),'TOOL_NOT_LISTED','EXPECTED_TOOL_MISSING')
        for t in defs:
            require(t.get('annotations',{}).get('readOnlyHint') is True and t.get('annotations',{}).get('destructiveHint') is False,'SCHEMA_MISMATCH','READONLY_ANNOTATIONS')
            require(t.get('inputSchema',{}).get('type')=='object' and 'projectPath' in t.get('inputSchema',{}).get('required',[]),'SCHEMA_MISMATCH','PROJECT_PATH_REQUIRED')
        return {t['name']:t for t in defs}
    def call(self,name,args,definitions):
        require(name in definitions,'TOOL_NOT_LISTED','CALL_BEFORE_DISCOVERY')
        validate_args(args,definitions[name]['inputSchema']);result=self.rpc('tools/call',{'name':name,'arguments':args})
        require(result.get('isError') is not True and isinstance(result.get('content'),list),'CALL_FAILED','TOOL_ERROR')
        require(all(x.get('type')=='text' and type(x.get('text')) is str for x in result['content']),'SCHEMA_MISMATCH','TOOL_CONTENT')
        return result
    def close(self):
        try:
            code,_,_=self.process.finish();require(code==0,'CALL_FAILED','MCP_EXIT_NONZERO')
        finally:self.process.close()
