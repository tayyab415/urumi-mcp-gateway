import subprocess
k=key('bob','claude')
config={'mcpServers':{'astra_review':{'type':'http','url':BASE+'/mcp','headers':{'Authorization':'Bearer '+k}}}}
fd=os.memfd_create('review-mcp-config',0);os.write(fd,json.dumps(config).encode());os.lseek(fd,0,0)
cmd=['claude','-p','--strict-mcp-config','--mcp-config',f'/proc/self/fd/{fd}','--no-session-persistence','--permission-mode','dontAsk','--tools','','--allowedTools','mcp__astra_review__woo__list_orders','mcp__astra_review__shipping__quote_shipping','mcp__astra_review__supplier__list_products','--output-format','json','--max-turns','5','--','Use the Urumi MCP tools now. List today\'s orders on the first WooCommerce store, quote shipping of a 1 kg parcel to IN 560001, and list supplier products. Report the observed order IDs, cheapest shipping option and supplier count. Do not run shell commands or write files.']
emit('Claude invocation','claude -p --strict-mcp-config --mcp-config <anonymous memory fd> --no-session-persistence --permission-mode dontAsk --tools "" --allowedTools <three read tools> --max-turns 5')
try:
 p=subprocess.run(cmd,cwd=ROOT/'docs/review',capture_output=True,text=True,pass_fds=(fd,),timeout=120)
 emit('exit',p.returncode);emit('stdout',p.stdout);emit('stderr',p.stderr)
finally:os.close(fd)
