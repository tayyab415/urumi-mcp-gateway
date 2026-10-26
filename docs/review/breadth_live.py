for who in CLIENTS:
 status,b,_=rpc(who)
 STATE[who+'_tools']=b.get('result',{}).get('tools',[]) if isinstance(b,dict) else []
 emit(who+' tools HTTP '+str(status),[t['name'] for t in STATE[who+'_tools']])
for who in ['admin','bob']:
 for path in ['/api/auth/me','/api/users','/api/servers','/api/tools','/api/stores','/api/audit?limit=3','/api/metrics','/api/keys?all=true','/api/oauth/grants?all=true']:
  status,b,_=api(who,path)
  if path=='/api/servers' and status==200:STATE[who+'_servers']=b
  if path.startswith('/api/audit') and isinstance(b,dict):STATE[who+'_audit']=b
  if isinstance(b,list): out=[{k:v for k,v in x.items() if k in ['id','slug','name','url','owner_id','status','user_email','public_name','outcome','error']} for x in b if isinstance(x,dict)][:10]
  elif isinstance(b,dict) and path=='/api/stores':out=b
  else:out=str(b)[:1200]
  emit(who+' '+path+' HTTP '+str(status),out)
for name in ['store-north__list_orders','woo__create_product','slack__post_message','nonexistent__tool']:
 emit('bob direct '+name,rpc('bob','tools/call',{'name':name,'arguments':{}})[:2])
for name,args in [('woo__list_products',{}),('woo__list_orders',{'period':'today'}),('shipping__list_carriers',{}),('supplier__list_products',{}),('slack__channel_info',{}),('store-south__list_orders',{'period':'today'}),('store-west-sports__list_orders',{'period':'today'})]:
 status,b,_=rpc('bob','tools/call',{'name':name,'arguments':args})
 emit('bob integration '+name+' HTTP '+str(status),str(b)[:2400])
for path in ['/.well-known/oauth-protected-resource/mcp','/.well-known/oauth-authorization-server']:
 emit('discovery '+path,Client().request(path)[:2])
for path,method,data in [('/mcp','POST',{}),('/mcp','GET',None),('/api/servers','GET',None),('/api/chat','POST',{'message':'hello'})]:
 status,b,h=Client().request(path,method,data)
 emit('unauthenticated '+method+' '+path,{'status':status,'body':b,'challenge':h.get('WWW-Authenticate')})
