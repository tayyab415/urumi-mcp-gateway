emit('Review-owned key records', [{'who': w, 'id': i} for w, i in CREATED])
emit('Stored state names', sorted(STATE))
emit('Stores', [
    {'slug': s['slug'], 'phase': s['phase'], 'owner': s['owner_email']}
    for s in api('admin', '/api/stores')[1]['stores']
])
emit('Servers', [
    {'id': s['id'], 'slug': s['slug'], 'enabled': s['enabled'], 'status': s['status']}
    for s in api('admin', '/api/servers')[1]
])
emit('Connected app grants', api('admin', '/api/oauth/grants?all=true')[1])
