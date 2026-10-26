import urllib.parse
for host in [BASE,RUN_BASE]:
    s,b,h=Client().request(host+'/mcp','POST',
        {'jsonrpc':'2.0','id':1,'method':'tools/call',
         'params':{'name':'review__no_such_tool','arguments':{}}},
        {'Authorization':'Bearer '+key('bob'),'Accept':'application/json, text/event-stream'})
    emit('Unknown tool '+host,{'status':s,'body':b})
    s,b,h=Client().request(host+'/mcp','POST',
        {'jsonrpc':'2.0','id':1,'method':'ping'},
        {'Accept':'application/json, text/event-stream'})
    emit('Unauthenticated challenge '+host,{'status':s,'body':b,
         'www-authenticate':next((v for k,v in h.items() if k.lower()=='www-authenticate'),None)})
for who in ['chen','bob','admin']:
    s,b,h=cloud_api(who,'/api/stores')
    emit('Stores after browser deletion '+who,{'http':s,'stores':[
         {'slug':x['slug'],'phase':x['phase']} for x in b['stores']]})
    s,b,h=Client().request(RUN_BASE+'/mcp','POST',
        {'jsonrpc':'2.0','id':1,'method':'tools/list'},
        {'Authorization':'Bearer '+key(who),'Accept':'application/json, text/event-stream'})
    emit('Removed review store tools '+who,{'http':s,'review_tools':[
         t['name'] for t in b.get('result',{}).get('tools',[]) if 'review-astra' in t['name']]})
for who in ['admin','bob','chen']:
    for path in ['/api/servers','/api/users','/api/audit','/api/tools','/api/chat/meta']:
        s,b,h=cloud_api(who,path)
        emit('Cloud Run page API '+who+' '+path,{'status':s})
s,b,h=cloud_api('admin','/api/servers')
emit('Final server statuses', [{'slug':x['slug'],'enabled':x['enabled'],'status':x['status']}
    for x in b])
s,b,h=cloud_api('admin','/api/oauth/grants?all=true')
emit('Final OAuth grants',{'status':s,'body':b})
