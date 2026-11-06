'use strict';
const fs=require('node:fs'),crypto=require('node:crypto'),net=require('node:net'),dgram=require('node:dgram');
const {spawn}=require('node:child_process');
const ROOT='/approved/toy',NODE='/opt/node';
const INDEX_ONLY=process.argv.length===3&&process.argv[2]==='--index-only';
const descriptor=JSON.parse(fs.readFileSync('/opt/descriptor.json','utf8'));
const report={scope:'PUBLIC_TOY_ONLY',stage:'safety',status:'NOT_VERIFIED',safety:{},requests:[],responses:[],graph_claim:'NOT_RUN'};
function check(value,label){if(!value)throw new Error(label);}
function sha(data){return crypto.createHash('sha256').update(data).digest('hex');}
function sourceHashes(){const out={};for(const f of ['math.js','app.js'])out[f]=sha(fs.readFileSync(ROOT+'/'+f));return out;}
function indexHashes(){const out={};function walk(p,key){for(const e of fs.readdirSync(p,{withFileTypes:true}).sort((a,b)=>a.name<b.name?-1:a.name>b.name?1:0)){const k=key+'/'+e.name;if(e.isDirectory())walk(p+'/'+e.name,k);else if(e.isFile())out[k]=sha(fs.readFileSync(p+'/'+e.name));else throw new Error('INDEX_SPECIAL_FILE');}}walk(ROOT+'/.codegraph','');return out;}
function deniedRead(p){try{fs.readFileSync(p);return false;}catch(e){return ['ENOENT','EACCES','EPERM'].includes(e.code);}}
function deniedWrite(p){try{fs.writeFileSync(p,'SYNTHETIC_WRITE_PROBE',{flag:'wx'});fs.unlinkSync(p);return false;}catch(e){return ['EROFS','EACCES','EPERM'].includes(e.code);}}
function deniedConnect(options){return new Promise(resolve=>{
 let s,settled=false;const done=value=>{if(settled)return;settled=true;clearTimeout(timer);if(s)s.destroy();resolve(value);};
 const timer=setTimeout(()=>done(false),1000);
 try{s=net.createConnection(options);s.once('connect',()=>done(false));s.once('error',e=>done(e.code==='EPERM'||e.code==='EACCES'));}catch(e){done(e.code==='EPERM'||e.code==='EACCES');}
});}
function deniedUdp(){return new Promise(resolve=>{
 const s=dgram.createSocket('udp4');let settled=false;
 const done=v=>{if(settled)return;settled=true;clearTimeout(t);try{s.close();}catch(_){}resolve(v);};
 const t=setTimeout(()=>done(false),1000);s.once('error',e=>done(e.code==='EPERM'||e.code==='EACCES'));
 try{s.send(Buffer.from('PUBLIC_PROBE'),descriptor.port,'127.0.0.1',e=>done(!!e&&(e.code==='EPERM'||e.code==='EACCES')));}catch(e){done(e.code==='EPERM'||e.code==='EACCES');}
});}
function spawnNode(script){
 // Only netgate-created FIFO descriptors are accepted; never create sockets.
 for(const fd of [3,4,5,6,7,8])check(fs.fstatSync(fd).isFIFO(),'PRIVATE_PIPE_TYPE');
 const child=spawn(NODE,['--liftoff-only','--disable-warning=ExperimentalWarning',script],{cwd:ROOT,env:Object.assign({},process.env),stdio:[3,6,8]});
 for(const fd of [3,6,8])fs.closeSync(fd);
 child.stdin=fs.createWriteStream(null,{fd:4});
 child.stdout=fs.createReadStream(null,{fd:5});
 child.stderr=fs.createReadStream(null,{fd:7});
 // With numeric stdio ChildProcess does not own these streams. Drain both
 // before parsing the index response or declaring clean MCP shutdown.
 child.pipesClosed=Promise.all([
  new Promise(resolve=>child.once('close',(code,signal)=>resolve({code,signal}))),
  ...[child.stdout,child.stderr].map(stream=>new Promise(resolve=>{
   stream.once('close',resolve);stream.on('error',()=>{child.kill('SIGKILL');});
  }))
 ]).then(([exit])=>exit);
 return child;
}
function runIndexer(){return new Promise((resolve,reject)=>{
 const child=spawnNode('/opt/index.cjs');child.stdin.on('error',()=>{});child.stdin.end();
 let out='',err='';const timer=setTimeout(()=>{child.kill('SIGKILL');reject(new Error('INDEX_TIMEOUT'));},120000);
 child.stdout.on('data',x=>{out+=x;if(out.length>8192)child.kill('SIGKILL');});
 child.stderr.on('data',x=>{err+=x;if(err.length>32768)child.kill('SIGKILL');});
 child.once('error',()=>{clearTimeout(timer);reject(new Error('INDEX_LAUNCH'));});
 child.pipesClosed.then(({code,signal})=>{clearTimeout(timer);report.index_exit={code,signal};if(code!==0||signal)return reject(new Error('INDEX_EXIT'));try{resolve(JSON.parse(out.trim()));}catch(_){reject(new Error('INDEX_RESPONSE'));}});
});}
function startMcp(){
 const child=spawnNode('/opt/adapter.cjs');
 let buffer='',bytes=0,stderr='',exited=false;const pending=new Map();
 const rejectAll=label=>{for(const x of pending.values()){clearTimeout(x.timer);x.reject(new Error(label));}pending.clear();};
 child.stdout.on('data',chunk=>{
  bytes+=chunk.length;if(bytes>262144){rejectAll('MCP_OUTPUT_LIMIT');child.kill('SIGKILL');return;}
  buffer+=chunk.toString();let at;
  while((at=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,at);buffer=buffer.slice(at+1);if(!line)continue;
   let msg;try{msg=JSON.parse(line);}catch(_){rejectAll('MCP_INVALID_JSON');child.kill('SIGKILL');return;}
   report.responses.push(msg);const item=pending.get(msg.id);
   if(!item){if(msg.method&&!('id'in msg))continue;rejectAll('MCP_UNEXPECTED_ID');return;}
   pending.delete(msg.id);clearTimeout(item.timer);
   if(msg.jsonrpc!=='2.0'||('error'in msg)||!msg.result)item.reject(new Error('MCP_ENVELOPE'));else item.resolve(msg.result);
  }
 });
 child.stderr.on('data',x=>{stderr+=x;if(stderr.length>65536){rejectAll('MCP_STDERR_LIMIT');child.kill('SIGKILL');}});
 child.once('error',()=>rejectAll('MCP_LAUNCH'));
 child.stdin.on('error',()=>rejectAll('MCP_STDIN'));
 child.once('exit',(code,signal)=>{exited=true;report.mcp_exit={code,signal};rejectAll('MCP_TRANSPORT_CLOSED');});
 function send(msg){report.requests.push(msg);child.stdin.write(JSON.stringify(msg)+'\n');}
 function rpc(id,method,params){return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);child.kill('SIGKILL');reject(new Error('MCP_TIMEOUT'));},45000);pending.set(id,{resolve,reject,timer});send({jsonrpc:'2.0',id,method,params});});}
 async function close(){child.stdin.end();let drained=false;await new Promise(resolve=>{const timer=setTimeout(()=>{child.kill('SIGKILL');resolve();},5000);child.pipesClosed.then(()=>{drained=true;clearTimeout(timer);resolve();});});check(drained&&exited&&report.mcp_exit.code===0&&!report.mcp_exit.signal,'MCP_CLEANUP');report.mcp_stderr=stderr;}
 return {rpc,send,close,abort:()=>child.kill('SIGKILL')};
}
function text(result){check(result&&!result.isError&&Array.isArray(result.content),'MCP_TOOL_ERROR');return result.content.map(c=>{check(c.type==='text'&&typeof c.text==='string','MCP_CONTENT');return c.text;}).join('\n');}
function validateArguments(args,schema){check(schema&&schema.type==='object'&&schema.properties,'SCHEMA');for(const key of schema.required||[])check(key in args,'REQUIRED_ARGUMENT');for(const [key,value]of Object.entries(args)){const rule=schema.properties[key];check(rule&&(rule.type==='string'?typeof value==='string':rule.type==='number'?typeof value==='number':false),'ARGUMENT_TYPE');if(rule.enum)check(rule.enum.includes(value),'ARGUMENT_ENUM');}}
(async()=>{
 const allowed=['HOME','LANG','CODEGRAPH_NO_DAEMON','CODEGRAPH_NO_WATCH','CODEGRAPH_NO_DOWNLOAD','CODEGRAPH_NO_UPDATE_CHECK','CODEGRAPH_TELEMETRY','DO_NOT_TRACK','CODEGRAPH_MCP_TOOLS','UV_USE_IO_URING'].sort();
 report.safety.environment=JSON.stringify(Object.keys(process.env).sort())===JSON.stringify(allowed)&&process.env.HOME==='/tmp';
 report.safety.nonroot=process.getuid()===65532;
 report.safety.runtime=process.version==='v24.22.2';
 report.safety.private_canaries=descriptor.private_paths.every(deniedRead);
 report.safety.no_host_home=!fs.existsSync('/home/runner')&&!fs.existsSync('/var/run/docker.sock');
 report.safety.root_readonly=deniedWrite('/opt/forbidden-write');
 report.safety.source_readonly=deniedWrite(ROOT+'/forbidden-write');
 if(!INDEX_ONLY)report.safety.index_readonly=deniedWrite(ROOT+'/.codegraph/forbidden-write');
 report.safety.source_identity=JSON.stringify(sourceHashes())===JSON.stringify(descriptor.source_hashes);
 report.safety.tcp_denied=await deniedConnect({host:'127.0.0.1',port:descriptor.port});
 report.safety.ipv6_denied=await deniedConnect({host:'::1',port:descriptor.port});
 report.safety.unix_denied=await deniedConnect({path:descriptor.unix_socket});
 report.safety.udp_denied=await deniedUdp();
 check(Object.values(report.safety).every(Boolean),'SAFETY_GATE_FAILED');
 check(require('/opt/codegraph/package.json').version==='1.6.2','CODEGRAPH_VERSION');
 if(INDEX_ONLY){
  report.stage='toy_index';report.index_stats=await runIndexer();
  check(JSON.stringify(sourceHashes())===JSON.stringify(descriptor.source_hashes),'SOURCE_MUTATED');
  report.index_digest=sha(JSON.stringify(indexHashes()));report.source_revision=descriptor.source_revision;
  report.graph_claim='TOY_INDEX_CREATED_ONLY';report.stage='complete';report.status='PASS';return;
 }
 const {CodeGraph}=require('/opt/codegraph/dist/index.js');
 const cg=CodeGraph.openSync(ROOT,{readOnly:true});try{report.index_stats=cg.getStats();}finally{cg.close();}
 check(report.index_stats.fileCount>=2&&report.index_stats.nodeCount>=2,'INDEX_STATS');
 const before=indexHashes();report.index_digest=sha(JSON.stringify(before));report.source_revision=descriptor.source_revision;report.source_hashes=sourceHashes();
 report.stage='real_mcp';const mcp=startMcp();
 try{
  const init=await mcp.rpc(1,'initialize',{protocolVersion:'2024-11-05',capabilities:{},clientInfo:{name:'public-toy-acceptance',version:'1'}});
  check(init.protocolVersion==='2024-11-05'&&init.serverInfo.name==='codegraph'&&init.serverInfo.version==='1.6.2','SERVER_IDENTITY');
  mcp.send({jsonrpc:'2.0',method:'notifications/initialized'});
  const listed=await mcp.rpc(2,'tools/list',{});check(!listed.nextCursor&&Array.isArray(listed.tools)&&listed.tools.length===3,'TOOLS_SURFACE');
  const names=listed.tools.map(t=>t.name).sort();check(JSON.stringify(names)===JSON.stringify(['codegraph_explore','codegraph_search','codegraph_status']),'TOOLS_NAMES');
  const calls=[['codegraph_status',{projectPath:ROOT}],['codegraph_search',{projectPath:ROOT,query:'computeInvoice',limit:10}],['codegraph_explore',{projectPath:ROOT,query:'computeInvoice addNumbers',maxFiles:2}]];
  const outputs={};let id=3;
  for(const [name,args]of calls){const def=listed.tools.find(t=>t.name===name);check(def.annotations.readOnlyHint===true&&def.annotations.destructiveHint===false,'READONLY_ANNOTATIONS');validateArguments(args,def.inputSchema);outputs[name]=text(await mcp.rpc(id++,'tools/call',{name,arguments:args}));}
  const match=outputs.codegraph_status.match(/\*\*Files indexed:\*\*\s*(\d+)/);check(match&&Number(match[1])>=2,'STATUS_UNRECOGNIZED');
  check(outputs.codegraph_search.includes('computeInvoice'),'SEARCH_EXPECTATION');
  check(outputs.codegraph_explore.includes('computeInvoice')&&outputs.codegraph_explore.includes('addNumbers'),'EXPLORE_EXPECTATION');
  await mcp.close();
 }catch(e){mcp.abort();throw e;}
 check(JSON.stringify(indexHashes())===JSON.stringify(before),'QUERY_MUTATED_INDEX');
 check(JSON.stringify(sourceHashes())===JSON.stringify(descriptor.source_hashes),'SOURCE_MUTATED');
 report.graph_claim='PUBLIC_TOY_INDEX_AND_THREE_MCP_TOOLS_VERIFIED';report.stage='complete';report.status='PASS';
})().then(()=>{process.stdout.write(JSON.stringify(report)+'\n');}).catch(e=>{report.status='FAIL';report.error=String(e.message).slice(0,160);process.stdout.write(JSON.stringify(report)+'\n');process.exitCode=1;});
