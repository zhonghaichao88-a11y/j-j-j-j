"""Build a clean V7.5 release from V7.4 without local account/runtime data."""
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
DESTINATION = ROOT.parent.parent / 'deliverables'
BASELINE = DESTINATION / 'ALPHA-X-V7.4.zip'
OUTPUT = DESTINATION / 'ALPHA-X-V7.5-PINE.zip'
EXCLUDED = {'.env', 'alpha_state.json', 'alpha_execution_fence.json', 'tv_alerts.json', '_check0.js', '_check1.js'}


def main():
    report = (ROOT / 'V75_TEST_RESULTS.txt').read_text(encoding='utf-8')
    assert report.rstrip().endswith('OK'), 'Tests must pass before packaging'
    tests = int(re.search(r'Ran (\d+) tests', report).group(1))
    replay = json.loads((ROOT / 'V75_REPLAY_RESULTS.json').read_text(encoding='utf-8'))
    assert replay['complete'] and len(replay['results']) == 30, 'Replay not complete'
    files = set()
    with ZipFile(BASELINE) as previous:
        for name in previous.namelist():
            relative = PurePosixPath(name).relative_to('ALPHA-X-V7')
            if not relative.parts or '..' in relative.parts:
                continue
            if relative.name in EXCLUDED or '.sqlite' in relative.name or relative.suffix in ('.db', '.pyc'):
                continue
            files.add(str(relative))
    files.update(p.name for p in ROOT.glob('V75_*') if p.is_file())
    files.update({'examples/V75_PINE_STRATEGY_EXIT.pine'})
    files.discard('V75_CHANGE_MANIFEST.json')
    manifest = dict(version='7.5-PINE-STRATEGY', tests=tests, replay_cases=30,
                    live_exchange_acceptance=False, browser_render_acceptance=False,
                    chan_rules='CX-74-strict-5bar-feature-gap',
                    pine='expanded compatibility subset; not full Pine parity',
                    excluded_runtime_data=True,
                    files={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(files)})
    (ROOT/'V75_CHANGE_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    files.add('V75_CHANGE_MANIFEST.json')
    with ZipFile(OUTPUT, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files):
            archive.write(ROOT/name, 'ALPHA-X-V7/'+name)
    with ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        for name, digest in manifest['files'].items():
            assert hashlib.sha256(archive.read('ALPHA-X-V7/'+name)).hexdigest() == digest
        names = set(archive.namelist())
        assert not any(PurePosixPath(name).name in EXCLUDED for name in names)
        assert 'ALPHA-X-V7/V75_READ_FIRST.md' in names
        assert 'ALPHA-X-V7/V75_TEST_RESULTS.txt' in names
    print(json.dumps(dict(file=str(OUTPUT), bytes=OUTPUT.stat().st_size, entries=len(files),
                         sha256=hashlib.sha256(OUTPUT.read_bytes()).hexdigest(), tests=tests, replay_cases=30)))


if __name__ == '__main__':
    main()
