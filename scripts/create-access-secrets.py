#!/usr/bin/env python3
"""Create ignored local credentials. Print file paths only, never credential values."""
from pathlib import Path
import hashlib
import os
import secrets

root=Path(__file__).resolve().parents[1]/'.secrets'
root.mkdir(mode=0o700,exist_ok=True)
root.chmod(0o700)
for name in ('mcp-token',):
    p=root/name
    if not p.exists():
        with p.open('x') as f:
            p.chmod(0o600)
            f.write(secrets.token_urlsafe(48)+'\n')
    else:
        p.chmod(0o600)
value=(root/'mcp-token').read_text().strip()
p=root/'mcp-token-sha256'
p.write_text(hashlib.sha256(value.encode()).hexdigest()+'\n');p.chmod(0o600)
print('Credentials stored in ignored .secrets/ with owner-only permissions. Values are not printed.')

p=root/'session-key'
if not p.exists():
    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:f.write(secrets.token_hex(32)+'\n')
