base_clean=clean
def clean(value):
 return re.sub(r'urumi_[A-Za-z0-9_.-]+','urumi_[redacted]',base_clean(value))
for path in PROOF.glob('*.txt'):
 txt=path.read_text();updated=clean(txt)
 if txt!=updated:path.write_text(updated)
print('All saved key prefixes reduced to first six characters.')
