'use strict';
const fs=require('node:fs');
const GATEWAY='/gateway';
if(process.cwd()!==GATEWAY||fs.lstatSync(GATEWAY).isSymbolicLink()||fs.readdirSync(GATEWAY).length!==0)
  throw new Error('GATEWAY_BASELINE');
const {resolveServerRoot}=require('/opt/codegraph/dist/directory.js');
const root=resolveServerRoot(GATEWAY);
if(root.root!==null||root.viaSubScan||root.candidates.length!==0)throw new Error('GATEWAY_INDEX_ADOPTION');
const base='/opt/codegraph/dist/mcp/';
const {MCPEngine}=require(base+'engine.js');
const {MCPSession}=require(base+'session.js');
const {StdioTransport}=require(base+'transport.js');
const engine=new MCPEngine({readOnly:true,watch:false,queryPool:false});
let session;
let stopping=false;
async function stop(){
  if(stopping)return;stopping=true;
  try{session.stop();await engine.stop();process.exit(0);}catch(_){process.exit(1);}
}
session=new MCPSession(new StdioTransport({exitOnClose:false,onClose:stop}),engine,
                       {explicitProjectPath:GATEWAY});
session.start();
process.on('SIGTERM',stop);process.on('SIGINT',stop);process.stdin.on('error',stop);
