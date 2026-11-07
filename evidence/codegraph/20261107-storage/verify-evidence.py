#!/usr/bin/env python3
"""Offline, read-only digest verification. Does not invoke C/F or network tools."""
import hashlib,json,pathlib
root=pathlib.Path(__file__).resolve().parent
rows=[]
for line in (root/'SHA256SUMS').read_text().splitlines():
    expected,name=line.split('  ',1)
    relative=pathlib.PurePosixPath(name)
    assert not relative.is_absolute() and '..' not in relative.parts
    path=root/name
    assert path.is_file() and not path.is_symlink()
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual==expected,name
    rows.append(name)
assert len(rows)==len(set(rows))
actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p!=root/'SHA256SUMS'}
assert actual==set(rows)
print(json.dumps({'result':'PASS','verified_files':len(rows),'scope':'local digest readback only','cross_cloud_retrieval':'NOT_VERIFIED','persistence':'EVIDENCE_NOT_PERSISTED'}))
