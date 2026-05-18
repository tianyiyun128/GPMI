from __future__ import annotations

import base64
import io
import zlib
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Iterable, Iterator


def normalize_resource_path(path: str | PurePosixPath) -> str:
    text = str(path).replace('\\', '/').strip('/')
    parts = []
    for part in PurePosixPath(text).parts:
        if part in ('', '.'):
            continue
        if part == '..':
            raise ValueError(f'Invalid embedded resource path: {path}')
        parts.append(part)
    return '/'.join(parts)


class EmbeddedResources:
    _files: dict[str, str] | None = None
    _dirs: set[str] | None = None

    @classmethod
    def _load_bundle(cls):
        if cls._files is not None:
            return
        try:
            from core import resources_bundle as bundle
        except Exception:
            cls._files = {}
            cls._dirs = set()
            return
        cls._files = {normalize_resource_path(k): v for k, v in getattr(bundle, 'FILES', {}).items()}
        dirs = {normalize_resource_path(x) for x in getattr(bundle, 'DIRS', []) if str(x).strip()}
        for rel in cls._files:
            parent = PurePosixPath(rel).parent
            while str(parent) not in ('', '.'):
                dirs.add(str(parent))
                parent = parent.parent
        cls._dirs = dirs

    @classmethod
    def enabled(cls) -> bool:
        cls._load_bundle()
        return bool(cls._files)

    @classmethod
    def is_file(cls, path: str | PurePosixPath) -> bool:
        cls._load_bundle()
        return normalize_resource_path(path) in cls._files

    @classmethod
    def is_dir(cls, path: str | PurePosixPath) -> bool:
        cls._load_bundle()
        return normalize_resource_path(path) in cls._dirs

    @classmethod
    def exists(cls, path: str | PurePosixPath) -> bool:
        return cls.is_file(path) or cls.is_dir(path)

    @classmethod
    @lru_cache(maxsize=2048)
    def read_bytes(cls, path: str | PurePosixPath) -> bytes:
        cls._load_bundle()
        rel = normalize_resource_path(path)
        try:
            payload = cls._files[rel]
        except KeyError as e:
            raise FileNotFoundError(f'Embedded resource not found: {rel}') from e
        return zlib.decompress(base64.b85decode(payload.encode('ascii')))

    @classmethod
    def read_text(cls, path: str | PurePosixPath, encoding: str = 'utf-8') -> str:
        return cls.read_bytes(path).decode(encoding)

    @classmethod
    def open_binary(cls, path: str | PurePosixPath) -> io.BytesIO:
        return io.BytesIO(cls.read_bytes(path))

    @classmethod
    def iter_files(cls, directory: str | PurePosixPath = '', suffixes: Iterable[str] | None = None) -> Iterator[str]:
        cls._load_bundle()
        rel_dir = normalize_resource_path(directory) if str(directory).strip() else ''
        prefix = f'{rel_dir}/' if rel_dir else ''
        suffix_set = {x.lower() for x in suffixes} if suffixes is not None else None
        for rel in sorted(cls._files):
            if prefix and not rel.startswith(prefix):
                continue
            if suffix_set and PurePosixPath(rel).suffix.lower() not in suffix_set:
                continue
            yield rel

    @classmethod
    def children(cls, directory: str | PurePosixPath = '') -> list[str]:
        cls._load_bundle()
        rel_dir = normalize_resource_path(directory) if str(directory).strip() else ''
        prefix = f'{rel_dir}/' if rel_dir else ''
        result = set()
        for rel in list(cls._files) + list(cls._dirs):
            if prefix and not rel.startswith(prefix):
                continue
            tail = rel[len(prefix):] if prefix else rel
            if tail and '/' not in tail:
                result.add(f'{prefix}{tail}' if prefix else tail)
        return sorted(result)


class EmbeddedResourcePath:
    def __init__(self, rel_path: str | PurePosixPath):
        self.rel_path = normalize_resource_path(rel_path)
        self._path = PurePosixPath(self.rel_path)

    @property
    def name(self) -> str:
        return self._path.name

    @property
    def suffix(self) -> str:
        return self._path.suffix

    @property
    def parent(self):
        return PurePosixPath(self.rel_path).parent

    def is_file(self) -> bool:
        return EmbeddedResources.is_file(self.rel_path)

    def exists(self) -> bool:
        return EmbeddedResources.exists(self.rel_path)

    def read_bytes(self) -> bytes:
        return EmbeddedResources.read_bytes(self.rel_path)

    def read_text(self, encoding: str = 'utf-8') -> str:
        return EmbeddedResources.read_text(self.rel_path, encoding)

    def open(self, mode: str = 'rb'):
        if 'b' in mode:
            return EmbeddedResources.open_binary(self.rel_path)
        return io.StringIO(self.read_text())

    def __str__(self) -> str:
        return f'embedded://{self.rel_path}'
