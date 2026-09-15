"""Verify an import changed serialization only, never model semantics or weights."""
import hashlib
import struct


def inspect(path):
    with path.open('rb') as f:
        def read(count):
            data = f.read(count)
            if len(data) != count: raise ValueError('truncated GGUF')
            return data
        def number(fmt): return struct.unpack('<' + fmt, read(struct.calcsize(fmt)))[0]
        def string():
            size = number('Q')
            if size > 32_000_000: raise ValueError('GGUF metadata string too large')
            return read(size)
        scalar = {0:'B', 1:'b', 2:'H', 3:'h', 4:'I', 5:'i', 6:'f', 7:'?', 10:'Q', 11:'q', 12:'d'}
        def value(kind, depth=0):
            if depth > 2: raise ValueError('GGUF nested metadata too deep')
            if kind in scalar: return number(scalar[kind])
            if kind == 8: return string()
            if kind == 9:
                item_type, count = number('I'), number('Q')
                if count > 1_000_000: raise ValueError('GGUF array too large')
                return (item_type, [value(item_type, depth + 1) for _ in range(count)])
            raise ValueError('unknown GGUF metadata type')
        if read(4) != b'GGUF' or number('I') != 3: raise ValueError('expected GGUF v3')
        tensor_count, metadata_count = number('Q'), number('Q')
        if tensor_count > 100_000 or metadata_count > 100_000: raise ValueError('GGUF counts too large')
        metadata = {}
        for _ in range(metadata_count):
            key, kind = string(), number('I')
            if key in metadata: raise ValueError('duplicate GGUF key')
            metadata[key] = (kind, value(kind))
        tensors = []
        for _ in range(tensor_count):
            name, dimensions = string(), number('I')
            if dimensions > 8: raise ValueError('invalid tensor dimensions')
            shape = [number('Q') for _ in range(dimensions)]
            tensors.append((name, shape, number('I'), number('Q')))
        alignment = metadata.get(b'general.alignment', (4, 32))[1]
        if not isinstance(alignment, int) or alignment < 1 or alignment > 4096:
            raise ValueError('invalid GGUF alignment')
        data_start = (f.tell() + alignment - 1) // alignment * alignment
        f.seek(data_start)
        payload_sha256 = hashlib.file_digest(f, 'sha256').hexdigest()
    return metadata, tensors, data_start, payload_sha256


def equivalent(source, imported):
    a, b = inspect(source), inspect(imported)
    if a != b or source.stat().st_size != imported.stat().st_size:
        raise ValueError('import changed metadata values, tensors, offsets or weight bytes')
    return {'transformation': 'metadata serialization order only',
            'metadata_entries': len(a[0]), 'tensors': len(a[1]),
            'payload_start': a[2], 'payload_sha256': a[3]}
