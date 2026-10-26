seen=set()
for who,ident in CREATED:
    if ident in seen:
        continue
    seen.add(ident)
    s,b,h=api(who,'/api/keys/'+str(ident),'DELETE')
    emit('Revoke review-created key',{'owner':who,'id':ident,'http':s})
for who in CLIENTS:
    s,b,h=api(who,'/api/keys')
    rows=b if isinstance(b,list) else b.get('keys',[])
    owned=[x for x in rows if x['id'] in seen]
    emit('Review key final state '+who,{'http':s,'keys':[
         {'id':x['id'],'revoked':bool(x.get('revoked_at'))} for x in owned]})
for host in [BASE,RUN_BASE]:
    s,b,h=Client().request(host+'/mcp','POST',
        {'jsonrpc':'2.0','id':1,'method':'ping'},
        {'Authorization':'Bearer '+KEYS['bob:main']['key'],'Accept':'application/json, text/event-stream'})
    emit('Revoked review key rejected '+host,{'http':s,'body':b})
emit('Review-created public OAuth registrations',{'count':len(STATE.get('oauth_clients',[])),
    'active_grants':cloud_api('admin','/api/oauth/grants?all=true')[1]})
for label,clients in [('direct',CLIENTS),('cloud',CLOUD_CLIENTS)]:
    for who,c in clients.items():
        host=BASE if label=='direct' else RUN_BASE
        s,b,h=c.request(host+'/api/auth/logout','POST')
        emit('Sign out '+label+' '+who,{'http':s})
print('No pre-existing key was revoked. Revoked key and audit records are retained by the application.')
