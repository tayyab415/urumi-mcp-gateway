for who in ['admin','bob']:
 status,b,_=api(who,'/api/audit?limit=100')
 rows=b if isinstance(b,list) else b.get('events',b.get('items',[]))
 emit(who+' audit schema',list(b) if isinstance(b,dict) else 'list')
 emit(who+' audit users', sorted(set(r.get('user_email','') for r in rows)))
 STATE[who+'_audit_before']=rows
for name,args in [('gateway__search_tools',{'query':'review-marker-audit products'}),('gateway__call_tool',{'name':'woo__list_products','arguments':{'search':'review-marker-audit'}})]:
 status,b,_=rpc('bob','tools/call',{'name':name,'arguments':args},path='/mcp/search')
 emit(name,{'status':status,'isError':b.get('result',{}).get('isError'),'size':len(str(b))})
status,b,_=api('bob','/api/audit?limit=10')
emit('audit after two search-mode calls',b)
