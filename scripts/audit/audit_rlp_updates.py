"""Compare saved RLP weights and isolate BF16 update rounding on CPU.

Memory: chunks contain <=262144 elements x <=64 bytes of working arrays
(16 MiB). AdamW controls use <=65536 elements x <=64 bytes (4 MiB).
Optimizer state uses torch.load(mmap=True); inspect metadata without loading
all moments. Runtime is capped externally at 2 GiB and one CPU core.
Synthetic gradients test numerical behavior, not replay of training gradients.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import urllib.request

import numpy as np
import torch


def header(path):
    with path.open('rb') as stream:
        length = struct.unpack('<Q', stream.read(8))[0]
        assert length < 1024**2
        metadata = json.loads(stream.read(length))
    return length + 8, {k: v for k, v in metadata.items() if k != '__metadata__'}


def floats(bits):
    return (bits.astype(np.uint32) << 16).view(np.float32)


def compare(before, after):
    assert before.shape == after.shape
    return {'elements': before.size, 'changed': int(np.count_nonzero(before != after))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--final', required=True, type=Path)
    args = parser.parse_args()
    torch.set_num_threads(1)
    assert torch.__version__.split('+')[0] == '2.7.0'
    out = args.output
    sources = json.loads((out / '.sources_private.json').read_text())
    provenance = {}
    for name, source in sources.items():
        digest = hashlib.sha256()
        size = 0
        with urllib.request.urlopen(source['url'], timeout=60) as response, (out / name).open('xb') as dest:
            while chunk := response.read(1024**2):
                dest.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        assert size == source['size']
        provenance[name] = {'s3': source['s3'], 'bytes': size, 'sha256': digest.hexdigest()}
        print('Downloaded', name, size, flush=True)
    (out / 'inputs.json').write_text(json.dumps(provenance, indent=2) + '\n')

    # Known equal and unequal arrays test both outcomes of the comparison.
    assert compare(np.array([1, 2]), np.array([1, 2]))['changed'] == 0
    assert compare(np.array([1, 2]), np.array([1, 3]))['changed'] == 1
    parent = out / 'parent.safetensors'
    start_a, heads_a = header(parent)
    start_b, heads_b = header(args.final)
    assert heads_a == heads_b
    total = changed = 0
    squared_delta = squared_parent = 0.0
    unchanged_tensors = []
    with parent.open('rb') as fa, args.final.open('rb') as fb, (out / 'tensors.jsonl').open('x') as records:
        for name, info in heads_a.items():
            assert info['dtype'] == 'BF16'
            lo, hi = info['data_offsets']
            assert hi - lo == int(np.prod(info['shape'])) * 2
            fa.seek(start_a + lo)
            fb.seek(start_b + lo)
            row = {'name': name, 'shape': info['shape'], 'elements': 0, 'changed': 0,
                   'delta_l2_squared': 0.0, 'parent_l2_squared': 0.0, 'max_abs_delta': 0.0}
            for offset in range(lo, hi, 2 * 262144):
                size = min(2 * 262144, hi - offset)
                a = np.frombuffer(fa.read(size), dtype='<u2')
                b = np.frombuffer(fb.read(size), dtype='<u2')
                counts = compare(a, b)
                x, y = floats(a), floats(b)
                assert np.isfinite(x).all() and np.isfinite(y).all()
                delta = y.astype(np.float64) - x
                row['elements'] += counts['elements']
                row['changed'] += counts['changed']
                row['delta_l2_squared'] += float(np.dot(delta, delta))
                row['parent_l2_squared'] += float(np.dot(x.astype(np.float64), x.astype(np.float64)))
                row['max_abs_delta'] = max(row['max_abs_delta'], float(np.abs(delta).max()))
            records.write(json.dumps(row) + '\n')
            records.flush()
            total += row['elements']
            changed += row['changed']
            squared_delta += row['delta_l2_squared']
            squared_parent += row['parent_l2_squared']
            if row['changed'] == 0:
                unchanged_tensors.append(name)

    saved = torch.load(out / 'optimizer.pt', map_location='cpu', mmap=True, weights_only=True)
    groups = [{k: v for k, v in group.items() if k != 'params'} | {'parameter_tensors': len(group['params'])}
              for group in saved['param_groups']]
    state_dtypes = Counter(str(value.dtype) for state in saved['state'].values()
                           for key, value in state.items() if key != 'step')
    state_keys = sorted({key for state in saved['state'].values() for key in state})
    lr = groups[0]['lr']
    assert all(group['lr'] == lr and group['weight_decay'] == 0 for group in groups)
    name = 'model.layers.0.self_attn.q_proj.weight'
    lo, hi = heads_a[name]['data_offsets']
    with parent.open('rb') as stream:
        stream.seek(start_a + lo)
        sample = torch.from_numpy(floats(np.frombuffer(stream.read(min(hi-lo, 2*65536)), dtype='<u2')).copy())

    # Same starting values, optimizer, steps, and gradients; vary parameter dtype.
    controls = []
    for gradient in (0.0, 1.0):
        for dtype in (torch.bfloat16, torch.float32):
            parameter = torch.nn.Parameter(sample.to(dtype).clone())
            optimizer = torch.optim.AdamW([parameter], lr=lr, betas=tuple(groups[0]['betas']),
                                         eps=groups[0]['eps'], weight_decay=0, foreach=False)
            first = None
            for step in range(256):
                parameter.grad = torch.full_like(parameter, gradient)
                optimizer.step()
                if step == 0:
                    first = int((parameter.detach().float() != sample).sum())
            result = {'dtype': str(dtype), 'gradient': gradient, 'steps': 256,
                      'n': sample.numel(), 'changed_after_one': first,
                      'changed_after_final': int((parameter.detach().float() != sample).sum()),
                      'max_abs_delta': float((parameter.detach().float() - sample).abs().max())}
            if gradient == 0:
                assert result['changed_after_final'] == 0
            else:
                assert result['changed_after_final'] > 0
            controls.append(result)
    summary = {'torch_version': torch.__version__, 'elements': total, 'changed': changed,
               'unchanged_fraction': (total-changed)/total, 'tensors': len(heads_a),
               'unchanged_tensors': unchanged_tensors,
               'relative_l2_change': (squared_delta/squared_parent)**0.5,
               'optimizer_groups': groups, 'optimizer_state_keys': state_keys,
               'optimizer_moment_dtypes': dict(state_dtypes), 'control_tensor': name,
               'controls': controls, 'control_scope': 'Synthetic constant gradients on saved weights; not a historical gradient replay.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
