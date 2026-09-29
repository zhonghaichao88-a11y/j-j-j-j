"""Pine v5 public API — compile, run, save, load, validate."""
from __future__ import annotations
import json, re, math, hashlib
from pathlib import Path
from .constants import PineError
from .parser import compile_source
from .runtime import Runtime


def validate_inputs(program, inputs):
    if not isinstance(inputs, dict) or len(inputs) > 100:
        raise PineError('inputs必须是最多100项的对象')
    if set(inputs) - set(program['input_names']):
        raise PineError(
            '未知input变量：'
            + ','.join(sorted(set(inputs) - set(program['input_names']))))
    for value in inputs.values():
        if (not isinstance(value, (str, bool, int, float))
                or isinstance(value, (int, float))
                and not math.isfinite(value)):
            raise PineError('input覆盖值必须是有限标量')


def run(source, frame, inputs=None, timeframe='5m', symbol='',
        frames=None):
    return Runtime(compile_source(source), frame, inputs, timeframe,
                   symbol, frames).run()


ROOT = Path(__file__).resolve().parent.parent / 'v7_pine_scripts'


def save(source, inputs=None):
    program = compile_source(source)
    validate_inputs(program, inputs or {})
    ROOT.mkdir(exist_ok=True)
    if inputs:
        payload = json.dumps(
            dict(format='v74-pine-profile', source=source, inputs=inputs),
            ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False)
        identifier = hashlib.sha256(payload.encode()).hexdigest()
        path = ROOT / (identifier + '.json')
    else:
        identifier = program['sha256']
        path = ROOT / (identifier + '.pine')
        payload = source
    if not path.exists():
        import tempfile, os
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=ROOT,
                delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return identifier


def load_profile(identifier):
    if not re.fullmatch('[0-9a-f]{64}', identifier or ''):
        raise PineError('脚本ID无效')
    path = ROOT / (identifier + '.json')
    profile = path.exists()
    if not profile:
        path = ROOT / (identifier + '.pine')
    if not path.exists():
        raise PineError('脚本不存在，请先导入并保存')
    source = path.read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest() != identifier:
        raise PineError('脚本内容哈希变化，请重新导入')
    if not profile:
        return source, {}
    data = json.loads(source)
    if data.get('format') != 'v74-pine-profile':
        raise PineError('Pine参数包格式无效')
    validate_inputs(compile_source(data['source']), data['inputs'])
    return data['source'], data['inputs']


def load(identifier):
    return load_profile(identifier)[0]


def run_saved(identifier, frame, timeframe='5m', symbol='', frames=None):
    source, inputs = load_profile(identifier)
    return run(source, frame, inputs, timeframe, symbol, frames)
