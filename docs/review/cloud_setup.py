RUN_BASE = 'https://urumi-1027824348124.us-central1.run.app'
old_cloud_get = Handler.do_GET
def cloud_get(self):
    if self.path.startswith('/bridge-cloud/'):
        who = self.path.rsplit('/', 1)[-1]
        if who not in CLIENTS:
            self.send_error(404)
            return
        page = ("<html><body>Signing in for the review.<script>fetch('/browser/"+who+
                "').then(r=>r.json()).then(x=>{window.opener.postMessage({reviewLogin:x},'"+
                RUN_BASE+"');window.close()})</script></body></html>")
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(page.encode())
        return
    return old_cloud_get(self)
Handler.do_GET = cloud_get
CLOUD_CLIENTS = {}
for who in CLIENTS:
    c = Client()
    s, b, h = c.request(RUN_BASE+'/api/auth/login', 'POST',
                        {'email': who+'@urumi.test', 'password': STATE[who+'_password']})
    SECRETS.extend(x.value for x in c.jar)
    emit('Cloud Run login '+who, {'status': s, 'role': b.get('role') if isinstance(b,dict) else None})
    CLOUD_CLIENTS[who] = c
def cloud_api(who, path, method='GET', data=None, headers=None):
    return CLOUD_CLIENTS[who].request(RUN_BASE+path,method,data,headers)
for host in [RUN_BASE,BASE]:
    s,b,h = Client().request(host+'/mcp','POST',
        {'jsonrpc':'2.0','id':1,'method':'tools/list'},
        {'Authorization':'Bearer '+key('bob'),'Accept':'application/json, text/event-stream'})
    emit('Existing key after planned upgrade '+host, {'status':s,'tools':
         [x['name'] for x in b.get('result',{}).get('tools',[])] if isinstance(b,dict) else b})
for path in ['/.well-known/oauth-authorization-server','/.well-known/oauth-protected-resource/mcp']:
    s,b,h = Client().request(RUN_BASE+path)
    emit(path, {'status':s,'body':b})
print('Installed Cloud Run browser bridge; credentials remain in memory.')
