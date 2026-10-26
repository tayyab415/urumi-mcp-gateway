import sys,asyncio,json,secrets,logging,time,struct,zlib,base64,random
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'services/gateway'))
import anyio,httpx2,mcp_types as mt
from contextlib import asynccontextmanager
from starlette.applications import Starlette
from starlette.routing import Route
from mcp import Client
from mcp.server import Server as MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.client.streamable_http import streamable_http_client
from gateway.config import Settings
from gateway.main import create_app
from gateway.mcp_endpoint import session_manager
from gateway.db import User,ApiKey,Server,AuditEvent
from gateway.security import new_api_key
from tests.test_gateway import serving,free_port
from sqlalchemy import select,func
MODE='text';NEW=False
def valid_large_png():
 def chunk(kind,data):
  return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data))
 pixels=random.Random(1).randbytes(320*320*3)
 rows=b''.join(b'\x00'+pixels[i:i+960] for i in range(0,len(pixels),960))
 png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',320,320,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')
 return base64.b64encode(png).decode()
PNG=valid_large_png()
async def listing(ctx,params):
 tools=[mt.Tool(name='payload',description='Return a result.',input_schema={'type':'object'},annotations=mt.ToolAnnotations(read_only_hint=True))]
 if NEW:tools.append(mt.Tool(name='late_tool',description='Added after discovery.',input_schema={'type':'object'},annotations=mt.ToolAnnotations(read_only_hint=True)))
 return mt.ListToolsResult(tools=tools)
async def calling(ctx,params):
 if MODE=='structured':return mt.CallToolResult(content=[mt.TextContent(type='text',text='ok')],structured_content={'payload':'x'*300000})
 if MODE=='image':return mt.CallToolResult(content=[mt.ImageContent(type='image',data=PNG,mime_type='image/png')])
 if MODE=='slow':await anyio.sleep(3)
 if MODE=='protocol':raise RuntimeError('fixture exploded')
 return mt.CallToolResult(content=[mt.TextContent(type='text',text='ok')])
async def main():
 global MODE,NEW
 logging.getLogger().setLevel(logging.CRITICAL)
 mgr=session_manager(MCPServer('review-upstream',on_list_tools=listing,on_call_tool=calling),[],[])
 @asynccontextmanager
 async def lifespan(app):
  async with mgr.run():yield
 up=Starlette(routes=[Route('/mcp',StreamableHTTPASGIApp(mgr),methods=['POST'])],lifespan=lifespan)
 port=free_port();base=f'http://127.0.0.1:{port}'
 cfg=Settings('sqlite+aiosqlite:///:memory:',secrets.token_hex(32),None,None,base,False,1,0,1,True)
 app=create_app(cfg)
 async with anyio.create_task_group() as tg:
  async with serving(tg,up) as (ub,_),serving(tg,app,port) as (gb,_):
   db=app.state.db
   raw,prefix,hashed=new_api_key()
   async with db.session() as s:
    u=User(email='review@example.invalid',name='Review',role='member',password_hash='unused');s.add(u);await s.flush();uid=u.id
    s.add(ApiKey(user_id=uid,name='ephemeral',prefix=prefix,key_hash=hashed))
    server=Server(slug='store-fixture',name='Store fixture',url=ub+'/mcp',managed_by='provisioner',owner_id=uid,timeout_seconds=1);s.add(server);await s.commit();sid=server.id
   await app.state.gateway.refresh_server(sid)
   async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+raw},timeout=10) as http:
    async with Client(streamable_http_client(gb+'/mcp',http_client=http),cache=None) as client:
     print('Real MCP SDK connected:',client.protocol_version)
     print('Initial tools:',[t.name for t in (await client.list_tools()).tools])
     NEW=True;await app.state.gateway.refresh_server(sid)
     print('Added upstream tool after first discovery, no admin approval. Member list:',[t.name for t in (await client.list_tools()).tools])
     late=await client.call_tool('store-fixture__late_tool',{})
     print('Member called unreviewed late tool: isError',late.is_error)
     for mode in ['structured','image','slow','protocol']:
      MODE=mode;t=time.monotonic();r=await client.call_tool('store-fixture__payload',{});payload=r.model_dump_json()
      print('Wire result',mode,'bytes',len(payload),'isError',r.is_error,'elapsed',round(time.monotonic()-t,2),'text',[c.text[:180] for c in r.content if isinstance(c,mt.TextContent)])
   tg.cancel_scope.cancel()
anyio.run(main)
