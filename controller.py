#!/usr/bin/env python3
"""GitHub-hosted public-only controller. No API tokens, original workspace or private checkout."""
import base64,contextlib,hashlib,json,os,pathlib,platform,re,shutil,socket,stat,struct,subprocess,tarfile,tempfile
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

def elf_interpreter(binary):
    # The checksum-pinned Node is ELF64 little-endian x86_64. Never guess from ldd.
    with binary.open('rb') as f:
        header=f.read(64)
        require(len(header)==64 and header[:6]==b'\x7fELF\x02\x01','ELF_FORMAT')
        require(struct.unpack_from('<H',header,18)[0]==62,'ELF_MACHINE')
        offset=struct.unpack_from('<Q',header,32)[0]
        size,count=struct.unpack_from('<HH',header,54)
        require(size==56 and 0<count<=128,'ELF_HEADERS')
        found=[]
        for i in range(count):
            f.seek(offset+i*size);entry=f.read(size);require(len(entry)==size,'ELF_TRUNCATED')
            kind,_,start,_,_,length,_,_=struct.unpack('<IIQQQQQQ',entry)
            if kind!=3:continue
            require(1<length<=4096,'ELF_INTERPRETER_SIZE')
            f.seek(start);raw=f.read(length)
            require(len(raw)==length and raw.endswith(b'\0') and b'\0' not in raw[:-1],'ELF_INTERPRETER_STRING')
            found.append(raw[:-1].decode('ascii'))
    require(found==['/lib64/ld-linux-x86-64.so.2'],'ELF_INTERPRETER_NOT_APPROVED')
    return found[0]

def verify_runtime(root,node,interpreter,dependencies):
    require(interpreter in dependencies,'ELF_INTERPRETER_MISSING')
    for path,expected in [(node,0o555)]+[(root/p.lstrip('/'),0o555 if p==interpreter else 0o444) for p in dependencies]:
        require(not path.is_symlink() and path.is_file(),'RUNTIME_FILE_TYPE')
        require(stat.S_IMODE(path.stat().st_mode)==expected,'RUNTIME_FILE_MODE')
        for parent in [root,*path.relative_to(root).parents]:
            parent=parent if parent==root else root/parent
            require(not parent.is_symlink() and parent.is_dir() and parent.stat().st_mode & 0o001,'RUNTIME_DIRECTORY')
    return {'interpreter':interpreter,'interpreter_mode':'0555','other_dependencies_mode':'0444','parent_traversal':True}

def seal_directories(root):
    # Only this newly assembled public rootfs; independent of host umask.
    for path in [root,*root.rglob('*')]:
        require(not path.is_symlink(),'ROOTFS_SYMLINK')
        if path.is_dir():path.chmod(0o555)

# Root helper is a fixed program, not a shell command. Walk absolute components
# with O_NOFOLLOW, bind work/index identities captured before chown, reject links
# and other devices, then use fd-relative deletion. It never reads file contents.
CLEANUP_INDEX=r'''
import os,re,shutil,stat,sys
def check(ok):
    if not ok:raise RuntimeError('CLEANUP_SCOPE_REJECTED')
def identity(st):return (st.st_dev,st.st_ino)
path=sys.argv[1];wd,wi,owner,di,ii=map(int,sys.argv[2:])
parts=path.split('/');check(parts[0]=='' and all(p not in ('','.','..') for p in parts[1:]))
check(re.fullmatch(r'cg-public-[a-z0-9_]{8}',parts[-1]) is not None)
flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
fd=os.open('/',flags)
try:
    for part in parts[1:]:
        child=os.open(part,flags,dir_fd=fd);os.close(fd);fd=child
    work=os.fstat(fd);check(identity(work)==(wd,wi) and work.st_uid==owner)
    index=os.stat('toy-index',dir_fd=fd,follow_symlinks=False)
    check(stat.S_ISDIR(index.st_mode) and identity(index)==(di,ii) and di==wd and index.st_uid in (owner,65532))
    def scan(directory):
        for name in os.listdir(directory):
            item=os.stat(name,dir_fd=directory,follow_symlinks=False)
            check(item.st_dev==wd and not stat.S_ISLNK(item.st_mode))
            check(stat.S_ISDIR(item.st_mode) or stat.S_ISREG(item.st_mode))
            if stat.S_ISDIR(item.st_mode):
                child=os.open(name,flags,dir_fd=directory)
                try:check(identity(os.fstat(child))==identity(item));scan(child)
                finally:os.close(child)
    child=os.open('toy-index',flags,dir_fd=fd)
    try:check(identity(os.fstat(child))==(di,ii));scan(child)
    finally:os.close(child)
    check(identity(os.stat('toy-index',dir_fd=fd,follow_symlinks=False))==(di,ii))
    check(shutil.rmtree.avoids_symlink_attacks)
    shutil.rmtree('toy-index',dir_fd=fd)
finally:os.close(fd)
'''

@contextlib.contextmanager
def public_workspace(parent,result):
    scratch=tempfile.TemporaryDirectory(prefix='cg-public-',dir=parent)
    work=pathlib.Path(scratch.name);st=work.stat()
    record={'path':work,'identity':(st.st_dev,st.st_ino,st.st_uid),'index_identity':None,'allow_cleanup':True}
    try:yield record
    finally:
        cleanup={'status':'PASS','errors':[]}
        if not record['allow_cleanup']:
            # Do not race a container whose removal could not be confirmed.
            scratch._finalizer.detach()
            cleanup={'status':'FAIL','errors':['CONTAINER_REMOVAL_UNCONFIRMED']}
        else:
            try:
                if record['index_identity'] is not None:
                    run(['sudo','-n','/usr/bin/python3','-I','-c',CLEANUP_INDEX,str(work),
                         *map(str,record['identity']),*map(str,record['index_identity'])])
            except Exception:
                cleanup['status']='FAIL';cleanup['errors'].append('INDEX_CLEANUP_FAILED')
            try:
                current=work.lstat()
                require((current.st_dev,current.st_ino,current.st_uid)==record['identity'] and not work.is_symlink(),'WORKSPACE_CHANGED')
                scratch.cleanup()
            except Exception:
                scratch._finalizer.detach()
                cleanup['status']='FAIL';cleanup['errors'].append('WORKSPACE_CLEANUP_FAILED')
        result['cleanup']=cleanup

def safe_error(error):
    if isinstance(error,RuntimeError) and re.fullmatch('[A-Z][A-Z0-9_]{0,79}',str(error)):return str(error)
    return type(error).__name__

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
    try:
        with public_workspace(os.environ['RUNNER_TEMP'],result) as workspace:
            work=workspace['path']
            context=work/'context';root=context/'rootfs';root.mkdir(parents=True)
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
                interpreter=elf_interpreter(node)
                deps=library_dependencies(node)
                deps.update(library_dependencies(root/'opt/codegraph/kernel/codegraph-kernel.node'))
                result['public_library_hashes']={dst:digest(src) for dst,src in sorted(deps.items())}
                require(interpreter in deps,'ELF_INTERPRETER_MISSING')
                for dst,src in deps.items():copy_public(src,root/dst.lstrip('/'),executable=dst==interpreter)
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
                seal_directories(root)
                result['runtime_layout']=verify_runtime(root,node,interpreter,deps)
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
                index_stat=index.stat();workspace['index_identity']=(index_stat.st_dev,index_stat.st_ino)
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
                        result['status']='FAIL';raise RuntimeError('WORKER_EXIT_'+phase.upper())
                    require('SECCOMP_ENFORCED' in p.stderr,'SECCOMP_ATTESTATION')
                    dr(['rm',name]);names.remove(name)
                require(result['phases']['index']['worker']['index_digest']==result['phases']['query']['worker']['index_digest'],'INDEX_PROVENANCE')
                result['status']='PUBLIC_TOY_SECURE_RUNNER_AND_REAL_MCP_VERIFIED'
            finally:
                listener.close();unix.close()
                if 'docker' in locals():
                    for name in names:
                        try:
                            removed=subprocess.run([*docker,'rm','-f',name],env=docker_env,capture_output=True,timeout=30)
                            if removed.returncode!=0:workspace['allow_cleanup']=False
                        except Exception:workspace['allow_cleanup']=False
    except Exception as error:
        result['status']='FAIL';result['primary_error']=safe_error(error)
    if result.get('cleanup',{}).get('status')!='PASS':result['status']='FAIL'
    # Emit only after cleanup, retaining both primary and cleanup failures.
    print(json.dumps(result,sort_keys=True))
    summary=os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:pathlib.Path(summary).write_text('## Public-only acceptance\n\n'+result['status']+'\n\nSource: `'+revision+'`\n')
    return 0 if result['status']=='PUBLIC_TOY_SECURE_RUNNER_AND_REAL_MCP_VERIFIED' else 1

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        # Avoid exposing arbitrary subprocess output or inherited environment.
        print(json.dumps({'scope':'PUBLIC_TOY_ONLY','status':'FAIL','error_type':type(error).__name__,'error':safe_error(error)}))
        raise SystemExit(1)
