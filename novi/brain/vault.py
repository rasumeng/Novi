"""Reconcile the editable vault into rebuildable knowledge and link indexes.

The manifest records only projection progress and prior revisions. Markdown
owns note identity/content; absence is a tombstone only for previously managed
notes. A failed pass never treats unreadable notes as deleted.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import re
import yaml
from datetime import datetime
from uuid import uuid4

from .types import KnowledgeForm, KnowledgeStatus, ReconcileReport


class VaultSynchronizer:
    def __init__(self, markdown, knowledge, index=None, relationships=None):
        self.markdown = markdown
        self.knowledge = knowledge
        self.index = index
        self.relationships = relationships
        self.db = sqlite3.connect(str(knowledge.store.persist_dir / 'vault.sqlite'), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY, path TEXT NOT NULL, digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS revisions (
                id TEXT NOT NULL, digest TEXT NOT NULL, metadata TEXT NOT NULL,
                body TEXT NOT NULL, recorded_at TEXT NOT NULL,
                PRIMARY KEY (id, digest)
            );
        ''')
        self.db.commit()
        self.db.execute('CREATE TABLE IF NOT EXISTS memory_edges '
                        '(source_id TEXT, target_id TEXT, kind TEXT, PRIMARY KEY(source_id,target_id,kind))')
        self.db.commit()
        self._files = {}

    @staticmethod
    def digest(meta, body):
        payload = json.dumps(meta, sort_keys=True, default=str) + '\n' + body
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def record(self, path):
        """Acknowledge a successful Brain write; remember its deletion boundary."""
        meta, body = self.markdown.parse(path)
        if not meta.get('id'):
            raise ValueError(f'Note has no identity: {path}')
        rel = path.relative_to(self.markdown.knowledge_dir).as_posix()
        digest = self.digest(meta, body)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO notes VALUES (?, ?, ?)', (str(meta['id']), rel, digest))
            self.db.execute('INSERT OR IGNORE INTO revisions VALUES (?, ?, ?, ?, ?)', (
                str(meta['id']), digest, json.dumps(meta, default=str), body, datetime.now().isoformat(),
            ))

    def sync(self):
        report = ReconcileReport()
        known = {r['id']: r for r in self.db.execute('SELECT * FROM notes')}
        if known and not self.markdown.knowledge_dir.is_dir():
            raise FileNotFoundError(f'Knowledge vault unavailable: {self.markdown.knowledge_dir}')
        indexed_ids = self.knowledge.store.ids()
        seen = set()
        # Parse the full snapshot before mutating anything; duplicate IDs and
        # unreadable files must not turn into accidental deletion or overwrite.
        snapshot = []
        for path in self.markdown.list_files():
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            cached = self._files.get(path)
            if cached is not None and cached[0] == stamp:
                _, meta, body = cached
                meta = dict(meta)
            else:
                raw = path.read_text('utf-8')
                if raw.startswith('---'):
                    frontmatter = re.match(r'^---\s*\n(.*?)\n---\s*(?:\n|$)', raw, re.DOTALL)
                    if frontmatter is None:
                        raise ValueError(f'Unclosed frontmatter in {path}')
                    parsed = yaml.safe_load(frontmatter.group(1))
                    if parsed is not None and not isinstance(parsed, dict):
                        raise ValueError(f'Frontmatter must be a mapping in {path}')
                meta, body = self.markdown.parse(path)
                self._files[path] = (stamp, dict(meta), body)
            kid = str(meta.get('id') or '')
            if kid and kid in seen:
                raise ValueError(f'Duplicate knowledge id {kid} in {path}')
            if kid:
                seen.add(kid)
            snapshot.append((path, meta, body))
        seen.clear()
        for path, meta, body in snapshot:
            report.scanned += 1
            if not meta.get('id'):
                meta.update(id=f'kn-{uuid4().hex}', status=KnowledgeStatus.VERIFIED.value,
                            confidence=1.0, source_kind='user_authored')
                meta['type'] = KnowledgeForm.COMPOSITE.value
                meta.setdefault('timestamp', datetime.now().isoformat())
                self.markdown._write_frontmatter(path, meta, body)
            kid = str(meta['id'])
            seen.add(kid)
            previous = known.get(kid)
            exists = kid in indexed_ids
            rel = path.relative_to(self.markdown.knowledge_dir).as_posix()
            changed = previous is None or previous['digest'] != self.digest(meta, body)
            moved = previous is not None and previous['path'] != rel
            if changed or moved or not exists:
                item = self.markdown.read_item(path)
                if item is None:
                    raise ValueError(f'Cannot reconstruct note {path}')
                if not body.strip():
                    item.status = KnowledgeStatus.SUPERSEDED
                    report.removed_claims += 1
                # Notes are authoritative, including their explicit status.
                self.knowledge.store.add(item, source_kind=str(meta.get('source_kind', 'user_authored')))
                if self.index is not None:
                    self.index.index_file(path)
                    if moved and hasattr(self.index, 'remove_file'):
                        self.index.remove_file(previous['path'])
                self.record(path)
                if previous is None or not exists:
                    report.new += 1
                else:
                    report.edited += 1
            else:
                report.unchanged += 1
        for kid, previous in known.items():
            if kid in seen:
                continue
            if not self.knowledge.update_status(kid, KnowledgeStatus.SUPERSEDED):
                if self.knowledge.store.get(kid) is not None:
                    raise RuntimeError(f'Could not retire deleted note {kid}')
            if self.index is not None and hasattr(self.index, 'remove_file'):
                self.index.remove_file(previous['path'])
            with self.db:
                self.db.execute('DELETE FROM notes WHERE id = ?', (kid,))
            report.missing_files += 1
        self._files = {path: value for path, value in self._files.items() if path.exists()}
        self._sync_memory_edges(snapshot)
        return report

    def _sync_memory_edges(self, snapshot):
        if self.relationships is None:
            return
        from .curation.packet import sections
        from .types import EdgeKind, Relationship
        existing_ids = {str(meta.get('id')) for _, meta, _ in snapshot}
        desired = set()
        for _, meta, body in snapshot:
            source = str(meta.get('id'))
            manifest = meta.get('memory_manifest', {})
            for section in sections(meta, body):
                record = manifest['sections'][section]
                conversation = record.get('conversation_id')
                if conversation:
                    desired.add((source, conversation, EdgeKind.DERIVED_FROM.value))
                target = record.get('link_id')
                if record.get('relation') == 'conflicts_with' and target in existing_ids and target != source:
                    desired.add((source, target, EdgeKind.CONFLICTS_WITH.value))
        owned = {tuple(row) for row in self.db.execute('SELECT source_id,target_id,kind FROM memory_edges')}
        for source, target, kind in owned - desired:
            self.relationships.remove(source_id=source, target_id=target, kind=EdgeKind(kind))
            with self.db:
                self.db.execute('DELETE FROM memory_edges WHERE source_id=? AND target_id=? AND kind=?',
                                (source, target, kind))
        for source, target, kind in desired:
            exists = any(edge.target_id == target for edge in self.relationships.outgoing(source, kind=EdgeKind(kind)))
            if not exists:
                # Own only edges this projection creates, preserving preexisting
                # explicit relationships. Record ownership before the edge for replay.
                with self.db:
                    self.db.execute('INSERT OR IGNORE INTO memory_edges VALUES (?,?,?)', (source, target, kind))
                self.relationships.add(Relationship(source_id=source, target_id=target, kind=EdgeKind(kind)))
