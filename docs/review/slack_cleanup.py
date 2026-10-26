import urllib.parse
result = STATE['review_slack_post']
channel, ts = result['channel'], result['ts']
token = json.loads((ROOT/'.runtime/app/secrets.json').read_text())['stringData']['slack-bot-token']
SECRETS.append(token)
s,b,h=Client().request(RUN_BASE+'/mcp','POST',
    {'jsonrpc':'2.0','id':1,'method':'tools/call',
     'params':{'name':'slack__read_messages','arguments':{'limit':2}}},
    {'Authorization':'Bearer '+key('admin'),'Accept':'application/json, text/event-stream'})
emit('Slack read-back before removing review message',{'status':s,'body':b})
payload=urllib.parse.urlencode({'channel':channel,'ts':ts}).encode()
s,b,h=Client().request('https://slack.com/api/chat.delete','POST',payload,
    {'Authorization':'Bearer '+token,'Content-Type':'application/x-www-form-urlencoded'})
emit('Delete only review message',{'status':s,'ok':b.get('ok'),'error':b.get('error')})
assert s==200 and b.get('ok'), 'Review Slack message cleanup failed'
s,b,h=Client().request(RUN_BASE+'/mcp','POST',
    {'jsonrpc':'2.0','id':1,'method':'tools/call',
     'params':{'name':'slack__read_messages','arguments':{'limit':2}}},
    {'Authorization':'Bearer '+key('admin'),'Accept':'application/json, text/event-stream'})
messages=b.get('result',{}).get('structuredContent',{}).get('messages',[])
emit('Review message absent after deletion',{'http':s,'absent':all(m['ts']!=ts for m in messages)})
