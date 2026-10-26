saved_base, saved_clients = BASE, CLIENTS
try:
    BASE, CLIENTS = RUN_BASE, CLOUD_CLIENTS
    exec(compile((ROOT/'docs/review/oauth_live.py').read_text(),
                 str(ROOT/'docs/review/oauth_live.py'),'exec'),globals())
finally:
    BASE, CLIENTS = saved_base, saved_clients
print('Restored review request defaults after Cloud Run OAuth tests.')
