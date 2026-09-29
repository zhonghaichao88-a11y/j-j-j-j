"""Build a clean release without copying local accounts or running-state files."""
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
DESTINATION = ROOT.parent.parent / 'deliverables'
BASELINE = DESTINATION / 'ALPHA-X-V7.3.zip'
OUTPUT = DESTINATION / 'ALPHA-X-V7.4.zip'
EXCLUDED = {'.env', 'alpha_state.json', 'alpha_execution_fence.json', 'tv_alerts.json', '_check0.js', '_check1.js'}


def main():
    report = (ROOT / 'V74_TEST_RESULTS.txt').read_text(encoding='utf-8')
    assert report.rstrip().endswith('OK'), 'Tests must pass before packaging'
    tests = int(re.search(r'Ran (\d+) tests', report).group(1))
    replay = json.loads((ROOT / 'V74_REPLAY_RESULTS.json').read_text(encoding='utf-8'))
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
    files.update(p.name for p in ROOT.glob('V74_*') if p.is_file())
    files.update({'tests/test_v74_completion.py', 'tests/check_v74_inputs.cjs', 'examples/V74_INPUT_STRATEGY.pine'})
    files.discard('V74_CHANGE_MANIFEST.json')
    manifest = dict(version='7.4-CHAN-PINE-FLOW', tests=tests, replay_cases=30,
                    live_exchange_acceptance=False, browser_render_acceptance=False,
                    chan_rules='CX-74-strict-5bar-feature-gap', pine='explicit compatibility subset',
                    excluded_runtime_data=True,
                    files={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(files)})
    (ROOT/'V74_CHANGE_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    files.add('V74_CHANGE_MANIFEST.json')
    with ZipFile(OUTPUT, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files):
            archive.write(ROOT/name, 'ALPHA-X-V7/'+name)
    with ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        for name, digest in manifest['files'].items():
            assert hashlib.sha256(archive.read('ALPHA-X-V7/'+name)).hexdigest() == digest
    print(json.dumps(dict(file=str(OUTPUT), bytes=OUTPUT.stat().st_size, entries=len(files),
                         sha256=hashlib.sha256(OUTPUT.read_bytes()).hexdigest(), tests=tests, replay_cases=30)))


if __name__ == '__main__':
    main()
