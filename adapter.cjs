'use strict';
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
                       {explicitProjectPath:'/approved/toy'});
session.start();
process.on('SIGTERM',stop);process.on('SIGINT',stop);process.stdin.on('error',stop);
