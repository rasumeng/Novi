"""Durable curation journal in the existing conversation database.

This store never loads a model or touches the vault. Shadow progress is distinct
from applied progress, so evaluation cannot consume production source evidence.
"""
import hashlib
import json
import sqlite3
from datetime import datetime
from threading import RLock
from time import time


class MemoryJobs:
    def __init__(self, path, clock=time):
        self.clock = clock
        self.lock = RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS memory_progress (
            conversation_id TEXT, mode TEXT, next_seq INTEGER NOT NULL,
            PRIMARY KEY(conversation_id, mode));
          CREATE TABLE IF NOT EXISTS memory_jobs (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, mode TEXT NOT NULL,
            start_seq INTEGER NOT NULL, next_seq INTEGER NOT NULL,
            state TEXT NOT NULL, packet TEXT NOT NULL, result TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
            lease_until REAL NOT NULL DEFAULT 0, updated REAL NOT NULL,
            error TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS memory_operations (
            job_id TEXT, operation_id TEXT, record TEXT NOT NULL,
            PRIMARY KEY(job_id, operation_id));
          CREATE TABLE IF NOT EXISTS memory_controls (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        self.db.commit()

    def claim(self, *, mode='shadow', turns=5, age_seconds=600, lease_seconds=1500):
        if mode not in ('shadow', 'apply') or turns < 1:
            raise ValueError('Invalid memory job policy')
        now = self.clock()
        with self.lock, self.db:
            # BEGIN IMMEDIATE serializes admission across connections as well.
            self.db.execute('BEGIN IMMEDIATE')
            # Older builds retried unchanged ambiguous evidence forever. Park
            # those exhausted batches before selecting work, without another
            # model call, and allow later source turns to make progress.
            exhausted = self.db.execute(
                "SELECT conversation_id,mode,next_seq FROM memory_jobs "
                "WHERE state='deferred' AND attempts>=3").fetchall()
            for old in exhausted:
                self.db.execute('INSERT INTO memory_progress VALUES (?,?,?) ON CONFLICT '
                                '(conversation_id,mode) DO UPDATE SET next_seq=MAX(next_seq,excluded.next_seq)',
                                (old['conversation_id'], old['mode'], old['next_seq']))
            self.db.execute("UPDATE memory_jobs SET state='parked', lease_until=0, "
                            "error=CASE WHEN error='' THEN 'Automatic retry limit reached' ELSE error END "
                            "WHERE state='deferred' AND attempts>=3")
            self.db.execute("UPDATE memory_jobs SET state='deferred', error='Expired lease', "
                            "lease_until=0 WHERE state IN ('proposing','verifying','approved','applying') "
                            "AND lease_until < ?", (now,))
            if self.db.execute("SELECT 1 FROM memory_jobs WHERE state IN "
                               "('proposing','verifying','approved','applying') AND lease_until>=? LIMIT 1",
                               (now,)).fetchone():
                return None
            row = self.db.execute("SELECT j.* FROM memory_jobs j WHERE j.mode=? "
                                  "AND j.state IN ('queued','deferred') AND j.retry_at<=? "
                                  "AND NOT EXISTS (SELECT 1 FROM memory_jobs b WHERE "
                                  "b.mode=j.mode AND b.conversation_id=j.conversation_id "
                                  "AND b.start_seq=j.start_seq AND "
                                  "COALESCE(json_extract(b.packet,'$.segment_index'),0) < "
                                  "COALESCE(json_extract(j.packet,'$.segment_index'),0) "
                                  "AND b.state NOT IN ('applied','shadow','abstained','rejected','parked')) "
                                  "ORDER BY j.updated LIMIT 1", (mode, now)).fetchone()
            if row is None:
                candidates = self.db.execute('''
                  SELECT t.conversation_id, COUNT(*) AS count, MIN(t.ts) AS oldest,
                    COALESCE(p.next_seq, e.next_seq, 0) AS start_seq
                  FROM turns t LEFT JOIN memory_progress p
                    ON p.conversation_id=t.conversation_id AND p.mode=?
                  LEFT JOIN extraction_progress e ON e.conversation_id=t.conversation_id
                  WHERE t.seq>=COALESCE(p.next_seq,e.next_seq,0)
                  GROUP BY t.conversation_id ORDER BY MIN(t.id)
                ''', (mode,)).fetchall()
                for candidate in candidates:
                    if candidate['count'] < turns:
                        if now - datetime.fromisoformat(candidate['oldest']).timestamp() < age_seconds:
                            continue
                    cid, start = candidate['conversation_id'], candidate['start_seq']
                    if self.db.execute("SELECT 1 FROM memory_jobs WHERE conversation_id=? AND mode=? "
                                       "AND state NOT IN ('applied','shadow','abstained','rejected','parked') LIMIT 1",
                                       (cid, mode)).fetchone():
                        continue
                    raw = self.db.execute('SELECT * FROM turns WHERE conversation_id=? AND seq>=? '
                                          'ORDER BY seq LIMIT ?', (cid, start, turns)).fetchall()
                    end = raw[-1]['seq'] + 1
                    job_id = hashlib.sha256(f'{mode}:{cid}:{start}:{end}'.encode()).hexdigest()
                    existing = self.db.execute('SELECT state FROM memory_jobs WHERE id=?', (job_id,)).fetchone()
                    if existing:
                        continue
                    conversation = self.db.execute('SELECT project_id FROM conversations WHERE id=?', (cid,)).fetchone()
                    spans = []
                    for turn in raw:
                        for actor, body in [('user', turn['user_text']), ('assistant', turn['assistant_text'])]:
                            if body:
                                spans.append({'id': f'{cid}:{turn["seq"]}:{actor}', 'actor': actor,
                                              'text': body, 'timestamp': turn['ts']})
                        for index, body in enumerate(json.loads(turn['tool_outputs'])):
                            if body:
                                spans.append({'id': f'{cid}:{turn["seq"]}:tool:{index}', 'actor': 'tool',
                                              'text': str(body), 'timestamp': turn['ts']})
                    # Persist every segment before evaluating any of them. Only
                    # the final segment can advance beyond the source batch.
                    segments = []
                    for span in spans:
                        for offset in range(0, len(span['text']), 1200):
                            segments.append(dict(span, id=f'{span["id"]}:{offset}',
                                                 original_start=offset,
                                                 text=span['text'][offset:offset+1200]))
                    chunks = [segments[i:i+4] for i in range(0, len(segments), 4)] or [[]]
                    for index, chunk in enumerate(chunks):
                        part_id = job_id if index == 0 else f'{job_id}-{index}'
                        packet = {'conversation_id': cid, 'project_id': conversation['project_id'],
                                  'turns': chunk, 'notes': [], 'segment_index': index,
                                  'segment_count': len(chunks)}
                        self.db.execute('INSERT INTO memory_jobs '
                                        '(id,conversation_id,mode,start_seq,next_seq,state,packet,updated) '
                                        "VALUES (?,?,?,?,?,'queued',?,?)",
                                        (part_id, cid, mode, start, end if index == len(chunks)-1 else start,
                                         json.dumps(packet), now + index * .000001))
                    row = self.db.execute('SELECT * FROM memory_jobs WHERE id=?', (job_id,)).fetchone()
                    break
            if row is None:
                return None
            self.db.execute("UPDATE memory_jobs SET state='proposing', attempts=attempts+1, "
                            "lease_until=?, updated=? WHERE id=?", (now+lease_seconds, now, row['id']))
            result = dict(row)
            result['packet'] = json.loads(result['packet'])
            result['attempts'] += 1
            return result

    def stage(self, job_id, state, *, packet=None, result=None):
        if state not in ('verifying', 'approved', 'applying'):
            raise ValueError('Invalid in-progress state')
        with self.lock, self.db:
            self.db.execute('UPDATE memory_jobs SET state=?, packet=COALESCE(?,packet), '
                            'result=COALESCE(?,result), updated=? WHERE id=?',
                            (state, json.dumps(packet) if packet is not None else None,
                             json.dumps(result) if result is not None else None, self.clock(), job_id))

    def finish(self, job, state, result=None, error=''):
        if state not in ('applied', 'shadow', 'abstained', 'rejected', 'deferred', 'parked'):
            raise ValueError('Invalid terminal state')
        if state == 'applied' and job['mode'] != 'apply':
            raise ValueError('Shadow job cannot apply memory')
        if state == 'shadow' and job['mode'] != 'shadow':
            raise ValueError('Apply job cannot consume a shadow result')
        with self.lock, self.db:
            self.db.execute('UPDATE memory_jobs SET state=?, result=?, error=?, updated=?, '
                            'lease_until=0,retry_at=? WHERE id=?',
                            (state, json.dumps(result), error, self.clock(),
                             self.clock() + min(3600, 30 * 2**min(job['attempts'], 6)), job['id']))
            if state != 'deferred':
                self.db.execute('INSERT INTO memory_progress VALUES (?,?,?) ON CONFLICT '
                                '(conversation_id,mode) DO UPDATE SET next_seq=MAX(next_seq,excluded.next_seq)',
                                (job['conversation_id'], job['mode'], job['next_seq']))

    def operation(self, job_id, operation_id, record=None):
        with self.lock, self.db:
            if record is not None:
                self.db.execute('INSERT OR REPLACE INTO memory_operations VALUES (?,?,?)',
                                (job_id, operation_id, json.dumps(record)))
            row = self.db.execute('SELECT record FROM memory_operations WHERE job_id=? AND operation_id=?',
                                  (job_id, operation_id)).fetchone()
            return json.loads(row[0]) if row else None

    def recent(self, limit=20):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                'SELECT id,conversation_id,mode,state,updated,error,result FROM memory_jobs '
                'ORDER BY updated DESC LIMIT ?', (limit,))]

    def control(self, key, value=None):
        with self.lock, self.db:
            if value is not None:
                self.db.execute('INSERT OR REPLACE INTO memory_controls VALUES (?,?)', (key, json.dumps(value)))
            row = self.db.execute('SELECT value FROM memory_controls WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def close(self):
        with self.lock:
            self.db.close()
