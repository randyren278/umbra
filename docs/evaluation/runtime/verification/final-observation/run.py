import importlib.metadata
import json
import os
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True
repo = Path('/Users/randyren/Developer/umbra')
task = Path('/private/var/folders/l0/f73q0dln26v1fk40dxfc_w9h0000gn/T/umbra-runtime-k_tqurzx')
sys.path.insert(0, str(repo / 'bench'))
import runtime
import tiktoken

freeze = json.loads((task / 'candidate-freeze-v6.json').read_text())
assert runtime.digest(freeze['binary']) == freeze['binarySha256']
assert runtime.digest(freeze['sdk']) == freeze['sdkSha256']
assert runtime.digest(runtime.__file__) == freeze['harnessSha256']
out = task / 'final-observation'
assert not out.exists()
with tempfile.TemporaryDirectory(prefix='umbra-closeout-tokenizer-') as cache:
    os.environ['TIKTOKEN_CACHE_DIR'] = cache
    runtime.TOKENIZER_VERSION = importlib.metadata.version('tiktoken')
    assert runtime.TOKENIZER_VERSION == '0.12.0'
    runtime.TOKENIZER = tiktoken.get_encoding('o200k_base')
    result = runtime.observation_journey(freeze['binary'], freeze['sdk'], out, 2048 * 2**20)
    runtime.write(out / 'verification.json', {'binarySha256': freeze['binarySha256'], 'sdkSha256': freeze['sdkSha256'], 'harnessSha256': runtime.digest(runtime.__file__), 'python': sys.executable, 'ok': result['ok'], 'summary': result['summary'], 'scope': 'Exact final-binary repeat of the frozen16-state actual SDK/CLI/MCP observation journey; all initial/navigation full responses included'})
    print(json.dumps({'ok': result['ok'], 'summary': result['summary'], 'cleanup': result['cleanup']}, sort_keys=True))
    assert result['ok'] and all(row['fiftyPercentGate'] for row in result['summary'].values())
