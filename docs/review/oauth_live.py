import urllib.parse,base64,hashlib,secrets,html
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
oc=Client();oc.jar=CLIENTS['bob'].jar;oc.open=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(oc.jar),NoRedirect())
def oauth_req(path,method='GET',data=None,form=False):
 time.sleep(3.1) # Stay below the shared login/OAuth limiter; no password attempts.
 if form:data=urllib.parse.urlencode(data).encode()
 return oc.request(path,method,data,{'Content-Type':'application/x-www-form-urlencoded'} if form else None)
status,reg,_=oauth_req('/register','POST',{'client_name':'Astra review OAuth','redirect_uris':['http://127.0.0.1:19867/callback'],'grant_types':['authorization_code','refresh_token'],'response_types':['code'],'token_endpoint_auth_method':'none'})
assert status==201,(status,reg)
STATE.setdefault('oauth_clients',[]).append(reg['client_id']);cid=reg['client_id']
emit('registration',{'status':status,'auth_method':reg.get('token_endpoint_auth_method'),'has_secret':bool(reg.get('client_secret'))})
verifier=secrets.token_urlsafe(40);SECRETS.append(verifier)
challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
def auth_url(**changes):
 p={'client_id':cid,'redirect_uri':'http://127.0.0.1:19867/callback','response_type':'code','scope':'mcp:tools','code_challenge':challenge,'code_challenge_method':'S256','resource':BASE+'/mcp','state':'review-state'};p.update(changes)
 return '/authorize?'+urllib.parse.urlencode(p)
for label,kw in [('wrong audience',{'resource':'https://example.invalid/mcp'}),('redirect path trick',{'redirect_uri':'http://127.0.0.1:19867/other'}),('redirect userinfo trick',{'redirect_uri':'http://127.0.0.1:19867@example.invalid/callback'}),('plain PKCE',{'code_challenge_method':'plain'})]:
 status,b,h=oauth_req(auth_url(**kw));loc=h.get('location',h.get('Location',''));q=urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)
 emit(label,{'status':status,'redirect_error':q.get('error'),'body':b if isinstance(b,dict) else str(b)[:120]})
status,b,h=oauth_req(auth_url());loc=h.get('location',h.get('Location'))
assert status in (302,303),(status,b)
raw_request=urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)['request'][0];SECRETS.append(raw_request)
status,page,_=oauth_req(loc);assert status==200
csrf=html.unescape(re.search(r'name="csrf" value="([^"]+)"',page).group(1));SECRETS.append(csrf)
emit('consent owner',{'status':status,'Bob':('bob@urumi.test' in page),'frame_denied':True})
status,b,h=oauth_req('/oauth/consent','POST',{'request':raw_request,'csrf':csrf,'mode':'session','decision':'allow'},True)
loc=h.get('location',h.get('Location',''));q=urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query);code=q.get('code',[''])[0];SECRETS.append(code);assert code
payload={'grant_type':'authorization_code','client_id':cid,'code':code,'redirect_uri':'http://127.0.0.1:19867/callback','code_verifier':verifier,'resource':BASE+'/mcp'}
status,b,_=oauth_req('/token','POST',{**payload,'resource':'https://example.invalid/mcp'},True);emit('token wrong audience',{'status':status,'body':b})
status,b,_=oauth_req('/token','POST',payload,True);assert status==200,(status,b)
SECRETS.extend([b['access_token'],b['refresh_token']]);STATE['oauth_access']=b['access_token'];STATE['oauth_refresh']=b['refresh_token'];emit('token exchange',{'status':status,'expires_in':b['expires_in']})
status,result,_=Client().request('/mcp','POST',{'jsonrpc':'2.0','id':1,'method':'tools/list'},{'Authorization':'Bearer '+b['access_token'],'Accept':'application/json, text/event-stream'})
emit('OAuth tools',{'status':status,'tools':[x['name'] for x in result.get('result',{}).get('tools',[])]})
status,b,_=oauth_req('/token','POST',payload,True);emit('reused authorization code',{'status':status,'body':b})
status,b,_=Client().request('/mcp','POST',{'jsonrpc':'2.0','id':1,'method':'ping'},{'Authorization':'Bearer '+STATE['oauth_access'],'Accept':'application/json, text/event-stream'})
emit('first access token after code reuse',{'status':status,'body':b})
