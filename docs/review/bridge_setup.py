old_get=Handler.do_GET
def bridge_get(self):
 if self.path.startswith('/bridge/'):
  who=self.path.split('/')[-1]
  if who not in CLIENTS:self.send_error(404);return
  page="<html><body>Signing in for the review.<script>fetch('/browser/"+who+"').then(r=>r.json()).then(x=>{window.opener.postMessage({reviewLogin:x},'"+BASE+"');window.close()})</script></body></html>"
  self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(page.encode());return
 return old_get(self)
Handler.do_GET=bridge_get
print('Installed ephemeral browser sign-in bridge. No credential persisted.')
