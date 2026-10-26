import subprocess
raw = key('bob','cloud-sdk-revocation')
x = {'base':RUN_BASE,'key':raw,
     'cookie':next(c.value for c in CLOUD_CLIENTS['bob'].jar if c.name=='urumi_session'),
     'id':KEYS['bob:cloud-sdk-revocation']['id']}
p = subprocess.run([str(ROOT/'services/gateway/.venv/bin/python'),
                    str(ROOT/'docs/review/sdk_revocation_client.py')],
                   input=json.dumps(x),capture_output=True,text=True,timeout=40)
emit('Cloud Run SDK exit',p.returncode)
emit('Cloud Run SDK output',p.stdout)
emit('Cloud Run SDK stderr',p.stderr)
for tool,args in [
    ('store-review-astra__update_order_status',{'order_id':16,'status':'completed'}),
    ('store-review-astra__get_order',{'order_id':16}),
    ('store-review-astra__update_order_status',{'order_id':16,'status':'processing'}),
    ('slack__channel_info',{}),('slack__read_messages',{'limit':2}),
    ('supplier__quote_restock',{'sku':'BALL-TRAIN','quantity':10}),
]:
    s,b,h=Client().request(RUN_BASE+'/mcp','POST',
        {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':tool,'arguments':args}},
        {'Authorization':'Bearer '+key('admin'),'Accept':'application/json, text/event-stream'})
    emit(tool,{'status':s,'body':b})
