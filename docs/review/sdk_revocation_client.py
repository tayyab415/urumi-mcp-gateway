import sys,json,asyncio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
x=json.load(sys.stdin)
async def main():
 async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+x['key']},timeout=20) as h:
  async with Client(streamable_http_client(x['base']+'/mcp',http_client=h),cache=None) as c:
   print('SDK protocol',c.protocol_version)
   print('Before revoke, tools',len((await c.list_tools()).tools))
   async with httpx2.AsyncClient(cookies={'urumi_session':x['cookie']}) as rest:
    r=await rest.delete(x['base']+'/api/keys/'+str(x['id']),headers={'X-Urumi-CSRF':'1'});print('Revoke HTTP',r.status_code)
   try:await c.list_tools();print('FAIL revoked key still works')
   except Exception as e:print('Next call on same SDK client refused:',type(e).__name__,str(e)[:250])
asyncio.run(main())
