import urllib.parse
message = '[review test] Astra gateway integration check. Temporary message, removed after verification.'
s,b,h=Client().request(RUN_BASE+'/mcp','POST',
    {'jsonrpc':'2.0','id':1,'method':'tools/call',
     'params':{'name':'slack__post_message','arguments':{'text':message}}},
    {'Authorization':'Bearer '+key('admin'),'Accept':'application/json, text/event-stream'})
emit('Slack post through gateway',{'status':s,'body':b})
result=b.get('result',{}).get('structuredContent')
if result is None:
    result=json.loads(b['result']['content'][0]['text'])
STATE['review_slack_post']=result
token=json.loads((ROOT/'.runtime/app/secrets.json').read_text())['stringData']['slack-bot-token']
SECRETS.append(token)
channel=result.get('channel')
ts=result.get('ts')
assert channel and ts,(channel,ts)
s,b,h=Client().request(RUN_BASE+'/mcp','POST',
    {'jsonrpc':'2.0','id':1,'method':'tools/call',
     'params':{'name':'slack__read_messages','arguments':{'limit':2}}},
    {'Authorization':'Bearer '+key('admin'),'Accept':'application/json, text/event-stream'})
emit('Slack read-back',{'status':s,'body':b})
payload=urllib.parse.urlencode({'channel':channel,'ts':ts}).encode()
s,b,h=Client().request('https://slack.com/api/chat.delete','POST',payload,
    {'Authorization':'Bearer '+token,'Content-Type':'application/x-www-form-urlencoded'})
emit('Delete only review message',{'status':s,'ok':b.get('ok'),'error':b.get('error')})
assert s==200 and b.get('ok'), 'Review Slack message cleanup failed'
