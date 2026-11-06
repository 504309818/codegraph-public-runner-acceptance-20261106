'use strict';
const fs=require('node:fs');
const {CodeGraph}=require('/opt/codegraph/dist/index.js');
(async()=>{
  const root='/approved/toy';
  if(fs.existsSync(root+'/.codegraph/codegraph.db'))throw new Error('NEW_TOY_INDEX_REQUIRED');
  const cg=await CodeGraph.init(root,{index:true});
  try{
    const stats=cg.getStats();
    if(stats.fileCount<2||stats.nodeCount<2)throw new Error('TOY_INDEX_EMPTY');
    process.stdout.write(JSON.stringify(stats)+'\n');
  }finally{cg.close();}
  // Public toy only: checkpoint and use a rollback journal so the second
  // container can query an OS-read-only index without SQLite WAL sidecars.
  const {DatabaseSync}=require('node:sqlite');
  const db=new DatabaseSync(root+'/.codegraph/codegraph.db');
  try{db.exec('PRAGMA wal_checkpoint(TRUNCATE); PRAGMA journal_mode=DELETE;');}finally{db.close();}
})().catch(()=>{process.stderr.write('TOY_INDEX_FAILED\n');process.exit(1);});
