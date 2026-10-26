# Safe protocol and role-denial probes. No existing object is mutated.
for method,params in [('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'astra-review','version':'1'}}),('ping',{}),('not/a/method',{}),('tools/call',{'name':'woo__list_orders','arguments':{'per_page':999}})]:
 emit('rpc '+method,rpc('bob',method,params)[:2])
for h in [{'Origin':'https://attacker.invalid'},{'Host':'attacker.invalid'},{'MCP-Protocol-Version':'not-supported'},{'Accept':'application/json'}]:
 emit('headers '+str(h),rpc('bob',headers=h)[:2])
for raw in [b'{',b'[]',b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"woo__list_orders","arguments":[]}}']:
 emit('invalid JSON/RPC '+raw.decode(),Client().request('/mcp','POST',raw,{'Authorization':'Bearer '+key('bob'),'Content-Type':'application/json','Accept':'application/json, text/event-stream'})[:2])
for path,method,data in [('/api/servers/7','GET',None),('/api/servers/7/tools','GET',None),('/api/servers/2','PATCH',{'enabled':True}),('/api/tools/woo__create_product','PATCH',{'allowed_roles':['member']}),('/api/users/3','PATCH',{'role':'admin'})]:
 emit('member endpoint '+method+' '+path,api('bob',path,method,data)[:2])
for host in ['http://169.254.169.254/latest/meta-data','http://[::ffff:169.254.169.254]/','http://127.0.0.1:8080/mcp','http://metadata.google.internal/','http://100.100.100.200/','http://10.43.0.1:443/']:
 emit('SSRF test '+host,api('admin','/api/servers/test','POST',{'slug':'review-test','url':host,'timeout_seconds':1})[:2])
# Use a dedicated disposable key for revocation.
raw=key('bob','revocation'); emit('before key revoke',rpc('bob',label='revocation')[0]); emit('revoke own test key',api('bob','/api/keys/'+str(KEYS['bob:revocation']['id']),'DELETE')[:2]);emit('next request revoked',rpc('bob',label='revocation')[:2])
# API key authentication is rejected on the dashboard API.
emit('API key at dashboard API',Client().request('/api/servers',headers={'Authorization':'Bearer '+key('bob')})[:2])
# Cookie does not authorize the MCP endpoint.
emit('session cookie at MCP',api('bob','/mcp','POST',{'jsonrpc':'2.0','id':1,'method':'tools/list'}, {'Accept':'application/json, text/event-stream'})[:2])
