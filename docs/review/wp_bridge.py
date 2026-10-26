wp_password=json.loads((ROOT/'.runtime/store/credentials.json').read_text())['stringData']['wordpress-admin-password']
SECRETS.append(wp_password)
STATE['wp_password']=wp_password
prior_wp_get=Handler.do_GET
def wp_get(self):
    if self.path=='/browser-wp':
        raw=json.dumps({'username':'urumi-admin','password':STATE['wp_password']}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw);return
    if self.path=='/bridge-wp':
        page="""<html><body>Signing in for the review.<script>fetch('/browser-wp').then(r=>r.json()).then(x=>{window.opener.postMessage({reviewWP:x},'https://shop.34-67-189-86.sslip.io');window.close()})</script></body></html>"""
        self.send_response(200);self.send_header('Content-Type','text/html')
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(page.encode());return
    return prior_wp_get(self)
Handler.do_GET=wp_get
print('First-store WordPress sign-in bridge ready. No credential persisted.')
