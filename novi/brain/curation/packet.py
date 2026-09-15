"""Bounded current-note snapshots, including intact machine section identities."""
import hashlib
import re


def digest(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode('utf-8')).hexdigest()


def section_marker(section_id):
    return f'<!-- novi-memory:{section_id} -->', '<!-- /novi-memory -->'


def sections(meta, body):
    result = {}
    for key, record in meta.get('memory_manifest', {}).get('sections', {}).items():
        if not re.fullmatch(r'[a-f0-9]{32}', key):
            continue
        start, end = section_marker(key)
        if body.count(start) != 1:
            continue
        _, _, tail = body.partition(start)
        content, separator, _ = tail.partition(end)
        if separator and digest(content.strip()) == record.get('content_sha256'):
            result[key] = content.strip()
    return result


def build_packet(packet, markdown, relevant_ids, *, max_bytes=24000):
    result = dict(packet, notes=[])
    wanted = set(relevant_ids)
    # Snapshot bytes, not a stale projected body. No source text is truncated.
    for path in markdown.list_files():
        resolved = path.resolve()
        if not resolved.is_relative_to(markdown.knowledge_dir):
            raise ValueError('Vault path escapes root')
        raw = path.read_bytes()
        meta, body = markdown.parse(path)
        if str(meta.get('id')) not in wanted:
            continue
        if path.read_bytes() != raw:
            raise ValueError('Note changed while snapshotting')
        result['notes'].append({'id': str(meta['id']), 'revision': digest(raw),
                                'path': path.relative_to(markdown.knowledge_dir).as_posix(),
                                'body': body, 'sections': sections(meta, body),
                                'claims': meta.get('memory_manifest', {}).get('sections', {})})
    import json
    if len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes:
        raise ValueError('Evidence packet too large; source work remains pending')
    return result
