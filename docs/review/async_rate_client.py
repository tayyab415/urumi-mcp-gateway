import asyncio,json,sys,time,collections
import httpx2
x=json.load(sys.stdin)
async def main():
 async with httpx2.AsyncClient(timeout=20,limits=httpx2.Limits(max_connections=40,max_keepalive_connections=40)) as h:
  async def call(auth):
   r=await h.post(x['base']+'/mcp',headers={'Authorization':auth,'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'ping'});return r.status_code
  t=time.monotonic();status=await asyncio.gather(*[call('Bearer '+x['key']) for _ in range(150)])
  print('150 identical requests',dict(collections.Counter(status)),'seconds',round(time.monotonic()-t,2))
  identical=await call('Bearer '+x['key']);print('same header immediately after burst',identical)
  variants=await asyncio.gather(*[call('Bearer '+(' '*i)+x['key']) for i in range(1,21)])
  print('same key with 20 distinct whitespace variants',dict(collections.Counter(variants)))
  print('control key',await call('Bearer '+x['control']))
asyncio.run(main())
