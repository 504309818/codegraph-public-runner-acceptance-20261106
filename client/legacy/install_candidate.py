#!/usr/bin/env python3
"""Offline, versioned candidate install; never edits Codex configuration/skills.

Only creates a new directory under workspace/tmp. Existing content must match;
there is no overwrite, network fetch, credential write or environment publish.
"""
import argparse,hashlib,json,os,pathlib,shutil,stat,tempfile

ALLOWED={'tool_access.py','mcp_client.py','registry.json','pins.json','native_snapshot.json',
         'install_candidate.py','README.zh-CN.md','skills/tool-access-review/SKILL.md'}
def require(ok,reason):
    if not ok:raise ValueError(reason)
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def install(root,expected,source=None):
    source=pathlib.Path(source or __file__).resolve().parent if source is None else pathlib.Path(source)
    manifest_path=source/'HASHES.json';require(not manifest_path.is_symlink(),'MANIFEST_SYMLINK')
    require(digest(manifest_path)==expected,'BUNDLE_DIGEST')
    manifest=json.loads(manifest_path.read_text());require(set(manifest)==ALLOWED,'MANIFEST_FILE_SET')
    for name,value in manifest.items():
        path=source/name;require(not path.is_symlink() and path.is_file() and digest(path)==value,'SOURCE_HASH')
        for parent in path.relative_to(source).parents:require(not (source/parent).is_symlink(),'SOURCE_PARENT_SYMLINK')
    root=pathlib.Path(root)
    require(root.is_absolute() and '..' not in root.parts and str(root).startswith(('/workspace/','/tmp/')),'INSTALL_ROOT')
    require(not any(p in {'.codex','.agents','.aws','.git','Codex-','Codex-Deliveries','codegraph-public-runner-acceptance-20261106'} for p in root.parts),'PROTECTED_ROOT')
    current=pathlib.Path(root.anchor)
    for part in root.parts[1:]:
        current=current/part
        require(not current.is_symlink(),'INSTALL_SYMLINK')
        if current.exists():require(current.is_dir(),'INSTALL_DIRECTORY')
        else:current.mkdir(mode=0o700)
    target=root/expected[:16]
    require(not target.is_symlink(),'TARGET_SYMLINK')
    if target.exists():
        require((target/'HASHES.json').is_file() and digest(target/'HASHES.json')==expected,'EXISTING_MANIFEST')
        for name,value in manifest.items():require(not (target/name).is_symlink() and digest(target/name)==value,'EXISTING_CONTENT')
        return {'status':'IDENTICAL_REUSED','target':str(target),'bundle_sha256':expected}
    stage=pathlib.Path(tempfile.mkdtemp(prefix='.cf-stage-',dir=root))
    try:
        for name in [*manifest,'HASHES.json']:
            dst=stage/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,dst);dst.chmod(0o444)
        # copytree creates target exclusively; it never replaces an existing
        # empty directory in the race between the existence check and publish.
        shutil.copytree(stage,target)
    finally:
        if stage.exists():shutil.rmtree(stage)
    return {'status':'INSTALLED_NEW_CANDIDATE_ONLY','target':str(target),'bundle_sha256':expected}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--expected-sha256',required=True);a=parser.parse_args()
    try:print(json.dumps(install(a.root,a.expected_sha256),ensure_ascii=False))
    except Exception:print(json.dumps({'status':'REFUSED','reason':'INSTALL_VALIDATION_FAILED'}));raise SystemExit(1)
