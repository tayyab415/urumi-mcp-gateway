"""Independent, bounded reproductions against the unmodified gateway code."""
import asyncio,json,sys,logging
from pathlib import Path
from unittest.mock import patch
import mcp_types as types
from gateway.core import Gateway,Identity,_capped,MAX_RESULT_CHARS,pending_review
from gateway.db import Database,Server,AuditEvent,utcnow
from gateway.security import CredentialBox
from gateway.upstreams import Discovery
from sqlalchemy import select,func
logging.disable(logging.CRITICAL)
ROOT=Path('/home/tayyabkhan/urumi-learning/docs/review/proof')
async def main():
 # Real database, real refresh, stub only the remote MCP discovery response.
 db=Database('sqlite+aiosqlite:///:memory:');await db.create_all();g=Gateway(db,CredentialBox('review-ephemeral-configuration'))
 for managed in [None,'provisioner']:
  server=Server(slug='fixture-'+('managed' if managed else 'manual'),name='Review fixture',url='http://example.invalid/mcp',managed_by=managed,owner_id=4 if managed else None,last_ok_at=utcnow(),tools=[])
  async with db.session() as sess:sess.add(server);await sess.commit();sid=server.id
  tool=types.Tool(name='new_export',description='New tool added after the first reviewed discovery.',input_schema={'type':'object'},annotations=types.ToolAnnotations(read_only_hint=True))
  async def discovery(target):return Discovery([tool],'2025-03-26',1)
  with patch('gateway.upstreams.discover',discovery):await g.refresh_server(sid)
  visible=await g.list_tools(Identity(4,'chen@urumi.test','member','mcp'))
  print('NEW TOOL after registration:',managed or 'manual','visible to member =',[t.name for t in visible])
 # The cap applies only if text crossed the limit.
 for label,result in [
  ('small text + oversized structuredContent',types.CallToolResult(content=[types.TextContent(type='text',text='ok')],structured_content={'payload':'x'*300000})),
  ('oversized image',types.CallToolResult(content=[types.ImageContent(type='image',data='A'*400000,mime_type='image/png')])),
  ('plain text control',types.CallToolResult(content=[types.TextContent(type='text',text='x'*300000)]))]:
  capped=_capped(result);print('RESULT CAP:',label,'serialized bytes',len(capped.model_dump_json()),'structured payload chars',len((capped.structured_content or {}).get('payload','')),'truncation marker',any('truncated' in getattr(c,'text','') for c in capped.content))
 await db.dispose()
asyncio.run(main())
