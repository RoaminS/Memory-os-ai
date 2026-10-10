"""Private evaluation-only model pool; fresh store per observable conversation.

No production tool or engine interface is changed. Only offline_server installs
this hook, inside a nonce-owned private stdio process.
"""
from __future__ import annotations

from dataclasses import replace
import gc
from pathlib import Path
import re
import shutil
import time


class ResidentPool:
    def __init__(self, memory_module, server_module, config):
        self.memory, self.server, self.config = memory_module, server_module, config
        self.parent = Path(config.data_dir).resolve().parent
        self.previous = Path(config.data_dir).resolve()
        self.conversations = self.parent / 'conversations'
        self.conversations.mkdir(exist_ok=False)
        self.seen = set()
        self.encoder = None
        self.reranker = None
        self.original_factory = memory_module._make_embedder
        self.original_dispatch = server_module.dispatch
        self.key = self.model_key(config)

    @staticmethod
    def model_key(config):
        return tuple(getattr(config, k) for k in
                     ('embed_model', 'device', 'embed_max_seq_length', 'embed_batch_size',
                      'embed_dtype', 'hash_dim'))

    def factory(self, config):
        if self.model_key(config) != self.key:
            raise ValueError('A resident arm cannot change its encoder configuration')
        if self.encoder is None:
            self.encoder = self.original_factory(config)
        return self.encoder

    def install(self):
        self.memory._make_embedder = self.factory
        self.server.dispatch = self.dispatch
        # SDK validates tool definitions. Register ONLY in the private server.
        from pydantic import BaseModel, ConfigDict, Field
        class OpenConversation(BaseModel):
            model_config = ConfigDict(extra='forbid')
            conversation_id: str = Field(pattern=r'^[0-9a-f]{24}$')
        self.server.TOOL_MODELS['eval_open_conversation'] = OpenConversation
        self.server.set_engine(self.memory.MemoryV4(config=self.config))
        if self.config.rerank_model:
            from memory_os_v4.rerank import CrossEncoderReranker
            self.reranker = CrossEncoderReranker(self.config.rerank_model, 'cpu')
            if not self.reranker.available():
                raise RuntimeError('Resident reranker unavailable; silent fallback refused')
            self.server.get_engine().retriever._reranker = self.reranker

    def open(self, conversation_id):
        if not isinstance(conversation_id, str) or not re.fullmatch(r'[0-9a-f]{24}', conversation_id):
            raise ValueError('Opaque observable conversation id required')
        if conversation_id in self.seen:
            raise ValueError('Conversation already attempted; uncertain mutations never retried')
        self.seen.add(conversation_id)
        t = time.perf_counter()
        self.server.set_engine(None)  # flush and release writer lease BEFORE removal
        previous = self.previous
        if previous.is_symlink() or previous.parent not in {self.parent, self.conversations}:
            raise ValueError('Refusing cleanup outside owned private conversation')
        if previous.exists():
            shutil.rmtree(previous)
        gc.collect()
        close_ms = (time.perf_counter() - t) * 1000
        target = self.conversations / conversation_id
        if target.exists() or target.is_symlink():
            raise ValueError('Fresh conversation store required')
        t = time.perf_counter()
        engine = self.memory.MemoryV4(config=replace(self.config, data_dir=str(target)))
        if self.reranker is not None:
            engine.retriever._reranker = self.reranker
        self.server.set_engine(engine)
        self.previous = target
        return {'ok': True, 'conversation_close_ms': close_ms,
                'conversation_init_ms': (time.perf_counter() - t) * 1000,
                'encoder_reused': engine.embedder is self.encoder, 'store_empty': not engine.store.items}

    def dispatch(self, name, arguments):
        if name != 'eval_open_conversation':
            return self.original_dispatch(name, arguments)
        try:
            parsed = self.server.TOOL_MODELS[name](**(arguments or {}))
            return self.open(parsed.conversation_id)
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}
