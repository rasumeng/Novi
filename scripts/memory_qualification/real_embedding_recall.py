"""Rebuild disposable trial notes with installed Ollama embeddings and query them."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from novi.brain import Brain
from novi.brain.layers.knowledge import KnowledgeLayer
from novi.brain.layers.scenarios import ScenarioLayer
from novi.brain.storage.conversation_store import ConversationStore
from novi.brain.storage.markdown_store import MarkdownStore
from novi.brain.storage.relationship_store import RelationshipStore
from novi.brain.storage.scenario_store import ScenarioStore
from novi.brain.storage.vector_store import VectorStore
from novi.services.embedding import EmbeddingService
from novi.runtime.retrieval_budget import ContextAllocation
from novi.runtime.sources.memory import MemoryRetrievalSource

from .disposable_trial import stop_owned
from .run import OwnedServer, api


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ollama-exe', type=Path, required=True)
    parser.add_argument('--model', default='nomic-embed-text:v1.5')
    parser.add_argument('--trial', type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.trial.read_text(encoding='utf-8').splitlines()]
    source = next(row for row in rows if row.get('id') == 'managed-02' and row.get('state') == 'applied')
    owner = OwnedServer(args.ollama_exe)
    try:
        owner.start()
        if not any(row['name'] == args.model for row in api('tags')['models']):
            raise RuntimeError('Embedding model is not installed; no pull attempted')
        with TemporaryDirectory(prefix='novi-memory-recall-') as directory:
            root = Path(directory)
            markdown = MarkdownStore(root / 'vault')
            for note in source['notes']:
                meta = {'id': note['id'], 'type': 'composite', 'status': note['status']}
                if note['manifest']:
                    meta['memory_manifest'] = note['manifest']
                markdown._write_frontmatter(markdown.knowledge_dir / note['path'], meta, note['body'])
            embedder = EmbeddingService({'embedding': {'backend': 'ollama', 'model': args.model,
                'url': 'http://127.0.0.1:11439', 'dimension': 768}})
            vectors = VectorStore(root / 'brain', embedder)
            scenarios = ScenarioStore(root / 'brain')
            conversations = ConversationStore(root / 'brain')
            relationships = RelationshipStore(root / 'brain')
            brain = Brain(markdown_store=markdown, knowledge_layer=KnowledgeLayer(vectors),
                          scenario_layer=ScenarioLayer(scenarios), conversation_store=conversations,
                          relationship_store=relationships, extractor=None)
            brain.reconcile_markdown()
            results = {}
            for label, query in [('work', 'What operating system does the user use at work?'),
                                 ('home', 'What operating system does the user use at home?')]:
                recalled = MemoryRetrievalSource(brain, distance_threshold=0.8).retrieve(
                    query, ContextAllocation(max_results=2))
                results[label] = {'quality': recalled.quality.value,
                    'items': [{'id': h.id, 'text': h.text, 'score': h.score,
                               'distance': h.metadata.get('distance')} for h in recalled.items]}
                context = '\n'.join(h.text for h in recalled.items)
                answer = api('chat', {'model': 'gemma4:e2b', 'stream': False, 'think': False,
                    'messages': [{'role': 'system', 'content':
                        'Answer from the supplied memory only. If it is absent, say unknown.'},
                        {'role': 'user', 'content': f'Memory:\n{context}\n\nQuestion: {query}'}],
                    'options': {'temperature': 0, 'num_predict': 64}}, timeout=90)
                results[label]['assistant_answer'] = answer.get('message', {}).get('content', '')
            print(json.dumps(results, indent=2))
            if ('Windows' not in results['work']['assistant_answer'] or
                    'Linux' not in results['home']['assistant_answer']):
                raise RuntimeError('Assistant answer did not use the expected recalled memory')
            vectors.close()
            scenarios.close()
            conversations.close()
            relationships.close()
            del brain, vectors
            import gc
            gc.collect()
    finally:
        stop_owned(owner)


if __name__ == '__main__':
    main()
