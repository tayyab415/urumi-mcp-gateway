matches=[]
for path in (ROOT/'docs/review').rglob('*'):
    if not path.is_file():
        continue
    data=path.read_bytes()
    for secret in SECRETS:
        if secret and len(secret)>=8 and secret.encode() in data:
            matches.append(str(path.relative_to(ROOT)))
            break
emit('Exact sensitive value scan',{'files_with_matches':sorted(set(matches)),
     'checked_files':sum(p.is_file() for p in (ROOT/'docs/review').rglob('*')),
     'credential_values_checked':len(SECRETS)})
assert not matches, 'A sensitive value needs removal before publishing the review'
