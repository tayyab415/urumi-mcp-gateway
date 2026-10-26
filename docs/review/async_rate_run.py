import subprocess
x={'base':BASE,'key':key('bob','async-rate-load'),'control':key('bob','async-rate-control')}
p=subprocess.run([str(ROOT/'services/gateway/.venv/bin/python'),str(ROOT/'docs/review/async_rate_client.py')],input=json.dumps(x),capture_output=True,text=True,timeout=45)
emit('exit',p.returncode);emit('stdout',p.stdout);emit('stderr',p.stderr)
