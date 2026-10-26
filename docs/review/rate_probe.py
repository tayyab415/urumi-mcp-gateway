from concurrent.futures import ThreadPoolExecutor
from collections import Counter
rkey=key('bob','rate-load');other=key('bob','rate-control')
def request_auth(value):
 return Client().request('/mcp','POST',{'jsonrpc':'2.0','id':1,'method':'ping'},{'Authorization':value,'Accept':'application/json, text/event-stream'})[0]
with ThreadPoolExecutor(max_workers=8) as pool:
 codes=list(pool.map(lambda i:request_auth('Bearer '+rkey),range(80)))
emit('80 requests, identical Authorization, same load key',dict(Counter(codes)))
emit('separate control key',request_auth('Bearer '+other))
with ThreadPoolExecutor(max_workers=8) as pool:
 codes=list(pool.map(lambda i:request_auth('Bearer '+(' '*(i+1))+rkey),range(30)))
emit('30 requests, same exhausted key, distinct extra spaces',dict(Counter(codes)))
