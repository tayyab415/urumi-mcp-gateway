"""A real subprocess executes the gateway route, applies a fixture write, then dies before replying."""
import sys,os,json,asyncio,sqlite3,subprocess,secrets,signal
from pathlib import Path
from unittest.mock import patch
import anyio,mcp_types as mt
from gateway.core import Gateway,Identity
from gateway.db import Database,User,Server,Tool,AuditEvent
from gateway.security import CredentialBox
from sqlalchemy import select,func
D=Path('/home/tayyabkhan/urumi-learning/docs/review/proof')
DB=D/'crash-fixture.db';MARK=D/'crash-fixture-write.txt'
async def worker():
 db=Database('sqlite+aiosqlite:///'+str(DB));g=Gateway(db,CredentialBox(secrets.token_hex(20)))
 async def write_then_hang(*args):
  MARK.write_text('fixture order committed\n');print('FIXTURE WRITE COMMITTED',flush=True);await anyio.sleep(60)
 with patch('gateway.upstreams.call_tool',write_then_hang):await g.call_tool(Identity(1,'review@example.invalid','admin','mcp'),'fixture__create_order',{'review_id':'crash-proof'})
async def setup():
 db=Database('sqlite+aiosqlite:///'+str(DB));await db.create_all()
 async with db.session() as s:
  s.add(User(id=1,email='review@example.invalid',name='Review',role='admin',password_hash='unused'))
  sv=Server(slug='fixture',name='Fixture',url='http://fixture.invalid',tools=[Tool(name='create_order',input_schema={'type':'object'},allowed_roles=['admin'],annotations={'readOnlyHint':False})]);s.add(sv);await s.commit()
 await db.dispose()
if sys.argv[-1]=='worker':asyncio.run(worker())
else:
 for p in [DB,MARK]:
  if p.exists():p.unlink()
 asyncio.run(setup())
 p=subprocess.Popen([sys.executable,__file__,'worker'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 assert p.stdout.readline().strip()=='FIXTURE WRITE COMMITTED'
 con=sqlite3.connect(DB);print('While write has committed: audit rows',con.execute('select count(*) from audit_events').fetchone()[0]);con.close()
 p.kill();p.wait(timeout=5)
 con=sqlite3.connect(DB);print('After gateway process SIGKILL: write marker exists',MARK.exists(),'audit rows',con.execute('select count(*) from audit_events').fetchone()[0]);con.close()
 print('Child exit',p.returncode,'Only the review-created subprocess was killed. No live Pod or store was changed.')
 DB.unlink();MARK.unlink()
