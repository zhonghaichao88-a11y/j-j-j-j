"""Read-only integrity verification. No exchange imports or account access."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1]
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 errors=[];count=0
 package=json.loads((ROOT/'V62_PACKAGE_FILES.json').read_text(encoding='utf8'))
 for rel,want in package['sha256'].items():
  p=ROOT/rel
  if not p.is_file() or digest(p)!=want:errors.append('Package mismatch: '+rel)
  count+=1
 for name in ('v62_results','v62_stress'):
  m=json.loads((ROOT/'backtest'/name/'manifest.json').read_text(encoding='utf8'))
  for rel,want in m['code'].items():
   if digest(ROOT/rel)!=want:errors.append('Research code mismatch: '+rel)
  for rel,want in m['data'].items():
   if digest(ROOT/'data_v62'/rel)!=want:errors.append('Research data mismatch: '+rel)
 if errors:
  print('\n'.join(errors));return 1
 print(f'PASS: {count} packaged files; both research manifests match code and all 33 data files.')
 return 0
if __name__=='__main__':sys.exit(main())
