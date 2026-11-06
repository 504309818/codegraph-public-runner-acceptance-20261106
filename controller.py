#!/usr/bin/env python3
"""GitHub-hosted public-only controller. No API tokens, original workspace or private checkout."""
import argparse,base64,hashlib,json,os,pathlib,platform,re,shutil,socket,subprocess,tarfile,tempfile,threading,time,urllib.request
BASE=pathlib.Path(__file__).resolve().parent
EXPECTED_REPO='504309818/codegraph-public-runner-acceptance-20261106'

def require(ok,label):
    if not ok:raise RuntimeError(label)
def run(args,**kw):
    return subprocess.run(args,check=True,text=True,capture_output=True,timeout=kw.pop('timeout',120),**kw)
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def safe_extract(archive,destination):
    with tarfile.open(archive,'r:gz') as t:
        for member in t.getmembers():
            require(not member.name.startswith('/') and '..' not in pathlib.PurePosixPath(member.name).parts,'ARCHIVE_PATH')
            require(member.isfile() or member.isdir(),'ARCHIVE_NONREGULAR')
        t.extractall(destination,filter='data')
def fetch(url,path,expected,algorithm):
    run(['curl','--fail','--silent','--show-error','--location','--proto','=https','--tlsv1.2','--max-time','120',url,'--output',str(path)],timeout=130)
    actual=hashlib.new(algorithm,path.read_bytes()).digest()
    require(actual==expected,'ARTIFACT_CHECKSUM')
def copy_public(src,dst,executable=False):
    require(src.is_file() and not src.is_symlink(),'PUBLIC_FILE')
    dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst);dst.chmod(0o555 if executable else 0o444)
def library_dependencies(binary):
    lines=run(['ldd',str(binary)]).stdout.splitlines();out={}
    require(not any('not found' in x for x in lines),'MISSING_LIBRARY')
    for line in lines:
        candidate=next((x for x in line.split() if x.startswith('/')),None)
        if candidate:out[candidate]=pathlib.Path(candidate).resolve()
    return out

def inspect_policy(info,root,readonly_index,expected_env):
    host=info['HostConfig'];cfg=info['Config']
    require(host['ReadonlyRootfs'] and host['NetworkMode']=='none','CONTAINER_ROOT_OR_NETWORK')
    require(host['Privileged'] is False and cfg['User']=='65532:65532','CONTAINER_IDENTITY')
    require(host.get('PidMode','')=='' and host.get('IpcMode','')=='private' and host.get('UTSMode','')=='','HOST_NAMESPACE')
    require('ALL' in [x.upper() for x in host.get('CapDrop',[])],'CAPABILITIES')
    require(not host.get('CapAdd') and not host.get('VolumesFrom'),'EXTRA_CAPABILITIES_OR_VOLUMES')
    require(any(x.startswith('no-new-privileges') for x in host.get('SecurityOpt',[])),'NO_NEW_PRIVILEGES')
    require(not any('unconfined' in x for x in host.get('SecurityOpt',[])),'UNCONFINED_POLICY')
    require(host.get('Memory',0)<=1073741824 and host.get('Memory',0)>0 and 0<host.get('PidsLimit',0)<=128,'RESOURCE_LIMITS')
    actual={x.split('=',1)[0]:x.split('=',1)[1] for x in cfg.get('Env',[])}
    # Only known fixed values are accepted. Never print unexpected names/values.
    require(actual==expected_env,'UNEXPECTED_CONTAINER_ENV')
    mounts=info.get('Mounts',[])
    binds=[x for x in mounts if x['Type']=='bind']
    require(all(x['Type']=='bind' or (x['Type']=='tmpfs' and x['Destination']=='/tmp') for x in mounts),'UNEXPECTED_VOLUME')
    require(len(binds)==1 and binds[0]['Source']==str(root) and binds[0]['Destination']=='/approved/toy/.codegraph','UNEXPECTED_BIND')
    require(binds[0]['RW'] is (not readonly_index),'INDEX_MOUNT_MODE')
    require(not host.get('Binds') and not host.get('Devices') and not host.get('DeviceRequests'),'EXTRA_HOST_ACCESS')
    return {'readonly_root':True,'network_none':True,'nonroot':True,'capabilities_dropped':True,
            'private_pid_ipc':True,'environment_allowlist':True,'only_toy_index_bind':True,'index_readonly':readonly_index}

def main():
    require(platform.machine()=='x86_64','X64_REQUIRED')
    require(os.environ.get('GITHUB_REPOSITORY')==EXPECTED_REPO,'PUBLIC_REPOSITORY_REQUIRED')
    revision=os.environ.get('GITHUB_SHA','');require(re.fullmatch('[0-9a-f]{40}',revision),'COMMIT_REQUIRED')
    pins=json.loads((BASE/'pins.json').read_text())
    result={'scope':'NEW_PUBLIC_TOY_ONLY','status':'NOT_VERIFIED','source_revision':revision,
            'runner':{'label':'ubuntu-24.04','image_os':os.environ.get('ImageOS','UNKNOWN'),'image_version':os.environ.get('ImageVersion','UNKNOWN')},'phases':{}}
    names=[]
    with tempfile.TemporaryDirectory(prefix='cg-public-',dir=os.environ['RUNNER_TEMP'],ignore_cleanup_errors=True) as tmp:
        work=pathlib.Path(tmp);context=work/'context';root=context/'rootfs';root.mkdir(parents=True)
        private=work/'private';private.mkdir(mode=0o700)
        private_paths=[]
        for name in ['fake-secret','fake-holdout']:
            p=private/name;p.write_text('SYNTHETIC_ONLY');private_paths.append(str(p))
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(2)
        port=listener.getsockname()[1]
        control=socket.create_connection(('127.0.0.1',port));peer,_=listener.accept();control.close();peer.close()
        unix=socket.socket(socket.AF_UNIX);unix_path=str(work/'fake-service.sock');unix.bind(unix_path);unix.listen(1)
        control=socket.socket(socket.AF_UNIX);control.connect(unix_path);peer,_=unix.accept();control.close();peer.close()
        try:
            node_archive=work/'node.tar.xz'
            run(['curl','--fail','--silent','--show-error','--location','--proto','=https','--max-time','120',pins['node_url'],'--output',str(node_archive)],timeout=130)
            require(digest(node_archive)==pins['node_archive_sha256'],'NODE_CHECKSUM')
            with tarfile.open(node_archive,'r:xz') as t:
                member=t.getmember('node-v24.22.2-linux-x64/bin/node');require(member.isfile(),'NODE_TYPE')
                node=root/'opt/node';node.parent.mkdir(parents=True);node.write_bytes(t.extractfile(member).read());node.chmod(0o555)
            cg_archive=work/'codegraph.tgz'
            fetch(pins['codegraph_url'],cg_archive,base64.b64decode(pins['codegraph_sha512']),'sha512')
            extracted=work/'cg';safe_extract(cg_archive,extracted)
            lib=extracted/'package/lib';require(json.loads((lib/'package.json').read_text())['version']=='1.6.2','CODEGRAPH_VERSION')
            shutil.copytree(lib,root/'opt/codegraph',symlinks=False)
            for p in (root/'opt/codegraph').rglob('*'):
                p.chmod(0o555 if p.is_dir() or p.suffix=='.node' else 0o444)
            deps=library_dependencies(node)
            deps.update(library_dependencies(root/'opt/codegraph/kernel/codegraph-kernel.node'))
            result['public_library_hashes']={dst:digest(src) for dst,src in sorted(deps.items())}
            for dst,src in deps.items():copy_public(src,root/dst.lstrip('/'))
            for name in ['worker.cjs','index.cjs','adapter.cjs']:copy_public(BASE/name,root/'opt'/name)
            run(['gcc','-static','-O2','-Wall','-Wextra','-Werror',str(BASE/'netgate.c'),'-o',str(root/'opt/netgate')])
            (root/'opt/netgate').chmod(0o555)
            toy=root/'approved/toy';toy.mkdir(parents=True)
            source_hashes={}
            for name in ['math.js','app.js']:
                copy_public(BASE/'toy'/name,toy/name);source_hashes[name]=digest(toy/name)
            (toy/'.codegraph').mkdir()
            descriptor={'private_paths':private_paths,'port':port,'unix_socket':unix_path,'source_revision':revision,'source_hashes':source_hashes}
            (root/'opt/descriptor.json').write_text(json.dumps(descriptor));(root/'opt/descriptor.json').chmod(0o444)
            (context/'Dockerfile').write_text('FROM scratch\nCOPY rootfs/ /\nUSER 65532:65532\nENTRYPOINT ["/opt/netgate"]\n')
            # Fresh client config prevents proxy/credential injection; no registry
            # config is read or modified and FROM scratch needs no pull.
            config=work/'docker-config';config.mkdir();(config/'config.json').write_text('{"auths":{},"proxies":{}}')
            docker=['docker','--host=unix:///var/run/docker.sock','--config',str(config)]
            docker_env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','HOME':str(config)}
            def dr(args,**kwargs):return run([*docker,*args],env=docker_env,**kwargs)
            dr(['info','--format','{{.ServerVersion}}'])
            tag='codegraph-public-acceptance:'+revision[:12]
            dr(['build','--network=none','--pull=false','-t',tag,str(context)],timeout=300)
            image=dr(['image','inspect',tag,'--format','{{.Id}}']).stdout.strip()
            result['worker_image']=image
            index=work/'toy-index';index.mkdir(mode=0o700)
            # Trusted Actions controller changes only this fresh public scratch
            # directory. No privileged container or host/private source bind.
            run(['sudo','-n','chown','65532:65532',str(index)])
            expected_env={'HOME':'/tmp','LANG':'C.UTF-8','PATH':'/usr/bin:/bin'}
            for phase,readonly in [('index',False),('query',True)]:
                name='cg-public-'+revision[:12]+'-'+phase;names.append(name)
                mount='type=bind,src='+str(index)+',dst=/approved/toy/.codegraph'+(',readonly' if readonly else '')
                args=[*docker,'create','--name',name,'--network=none','--ipc=private','--read-only','--user=65532:65532',
                      '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=96','--memory=1g','--cpus=2',
                      '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777',
                      '--env','HOME=/tmp','--env','LANG=C.UTF-8','--env','PATH=/usr/bin:/bin','--mount',mount,image]
                if phase=='index':args.append('--index-only')
                run(args,env=docker_env)
                info=json.loads(dr(['inspect',name]).stdout)[0]
                audit=inspect_policy(info,index,readonly,expected_env)
                p=subprocess.run([*docker,'start','--attach',name],env=docker_env,text=True,capture_output=True,timeout=240)
                require(len(p.stdout.encode())<=524288 and len(p.stderr.encode())<=65536,'WORKER_OUTPUT_LIMIT')
                exit_code=int(dr(['inspect',name,'--format','{{.State.ExitCode}}']).stdout.strip())
                try:report=json.loads(p.stdout.strip())
                except ValueError:report={'status':'INVALID_JSON'}
                result['phases'][phase]={'controller_policy':audit,'worker_exit':exit_code,'attach_exit':p.returncode,'worker':report,'helper_stderr':p.stderr[:8192]}
                if p.returncode!=0 or exit_code!=0 or report.get('status')!='PASS':
                    result['status']='FAIL';print(json.dumps(result,sort_keys=True));raise RuntimeError('WORKER_EXIT_'+phase)
                require('SECCOMP_ENFORCED' in p.stderr,'SECCOMP_ATTESTATION')
                dr(['rm',name]);names.remove(name)
            require(result['phases']['index']['worker']['index_digest']==result['phases']['query']['worker']['index_digest'],'INDEX_PROVENANCE')
            result['status']='PUBLIC_TOY_SECURE_RUNNER_AND_REAL_MCP_VERIFIED'
        finally:
            listener.close();unix.close()
            if 'docker' in locals():
                for name in names:
                    subprocess.run([*docker,'rm','-f',name],env=docker_env,capture_output=True,timeout=30)
        # Only public toy facts/transcript are output, never environment dumps.
        print(json.dumps(result,sort_keys=True))
        summary=os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:pathlib.Path(summary).write_text('## Public-only acceptance\n\n'+result['status']+'\n\nSource: `'+revision+'`\n\nWorker: `'+result['worker_image']+'`\n')

if __name__=='__main__':
    try:main()
    except Exception as error:
        # Avoid exposing arbitrary subprocess output or inherited environment.
        print(json.dumps({'scope':'PUBLIC_TOY_ONLY','status':'FAIL','error_type':type(error).__name__,'error':str(error).splitlines()[0][:120]}))
        raise SystemExit(1)
