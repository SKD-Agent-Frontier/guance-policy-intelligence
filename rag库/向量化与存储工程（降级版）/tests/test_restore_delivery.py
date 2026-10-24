"""Release交付的分卷校验、原文保真、路径重定位和本地配置绑定。"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import restore_delivery as delivery


def make_package(root):
    root.mkdir()
    generation = 'local_generation_test'
    manifest = {'prepared_path': delivery.PORTABLE_ROOT + '/outputs/prepared_test',
                'media_path': delivery.PORTABLE_ROOT + '/outputs/media_test',
                'profile': {'service_url': delivery.EMBEDDING_URL}}
    database = root / 'metadata.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE releases (id TEXT PRIMARY KEY, manifest TEXT)')
        db.execute('INSERT INTO releases VALUES (?,?)', (generation, json.dumps(manifest)))
    files = {'.runtime/local/metadata.sqlite3': database.read_bytes(),
             'outputs/' + generation + '/manifest.json': json.dumps(manifest).encode(),
             'outputs/prepared_test/source.md': '第一章\n原文保留：😀\n'.encode()}
    archive = root / 'runtime.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in files.items():
            package.writestr(name, data)
        package.writestr('backup_manifest.json', json.dumps({
            'project_root': delivery.PORTABLE_ROOT, 'generation_id': generation,
            'files': [{'path': name, 'sha256': hashlib.sha256(data).hexdigest()} for name, data in files.items()]}))
    raw = archive.read_bytes()
    parts = []
    for index, data in enumerate((raw[:len(raw)//2], raw[len(raw)//2:]), 1):
        name = 'runtime.zip.part00' + str(index)
        (root / name).write_bytes(data)
        parts.append({'name': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    public = {'archive': {'name': archive.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}, 'parts': parts}
    path = root / 'delivery-manifest.json'
    path.write_text(json.dumps(public), encoding='utf-8')
    archive.unlink()  # 强制测试从分卷重新合并，不能复用刚生成的ZIP。
    return path, files


def test_release_parts_restore_and_local_configuration(tmp_path):
    manifest, original = make_package(tmp_path / 'download')
    archive, _ = delivery.assemble(manifest)
    destination = tmp_path / '新目录'
    result = delivery.restore(archive, destination)
    assert result['files_verified'] == 3
    assert (destination / 'outputs/prepared_test/source.md').read_bytes() == original['outputs/prepared_test/source.md']
    with sqlite3.connect(destination / '.runtime/local/metadata.sqlite3') as db:
        restored = json.loads(db.execute('SELECT manifest FROM releases').fetchone()[0])
    assert restored['prepared_path'] == str(destination / 'outputs/prepared_test')
    assert restored['profile']['service_url'] == delivery.EMBEDDING_URL
    local = tmp_path / 'private.json'
    local.write_text(json.dumps({'embedding_base_url': 'http://localhost:9999/v1', 'embedding_api_key': 'local-fixture'}), encoding='utf-8')
    delivery.configure(destination, local)
    with sqlite3.connect(destination / '.runtime/local/metadata.sqlite3') as db:
        configured = json.loads(db.execute('SELECT manifest FROM releases').fetchone()[0])
    assert configured['profile']['service_url'] == 'http://localhost:9999/v1'
    assert (destination / 'secrets.local.json').read_bytes() == local.read_bytes()
    assert delivery.read(destination / 'outputs/local_generation_test/manifest.json')['profile'] == configured['profile']
    with pytest.raises(ValueError, match='must not exist'):
        delivery.restore(archive, destination)


def test_tampered_part_rejected_before_assembly(tmp_path):
    manifest, _ = make_package(tmp_path / 'download')
    part = manifest.parent / 'runtime.zip.part001'
    data = bytearray(part.read_bytes()); data[0] ^= 1; part.write_bytes(data)
    with pytest.raises(ValueError, match='checksum mismatch'):
        delivery.assemble(manifest)
    assert not (manifest.parent / 'runtime.zip').exists()


def test_zip_traversal_rejected_before_destination_created(tmp_path):
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr('../outside', b'x')
        package.writestr('backup_manifest.json', json.dumps({'project_root': delivery.PORTABLE_ROOT,
            'files': [{'path': '../outside', 'sha256': hashlib.sha256(b'x').hexdigest()}]}))
    destination = tmp_path / 'restored'
    with pytest.raises(ValueError, match='unsafe archive path'):
        delivery.restore(archive, destination)
    assert not destination.exists()


def test_fresh_restore_long_path_does_not_resolve_concurrent_children(tmp_path, monkeypatch):
    manifest, _ = make_package(tmp_path / 'download')
    archive, _ = delivery.assemble(manifest)
    destination = tmp_path / ('长路径' * 12) / 'restored'
    original_resolve = Path.resolve

    def resolve_existing_root_only(path, *args, **kwargs):
        # destination创建后，子目录正被其他线程创建；恢复阶段不应再依赖其realpath。
        if destination.exists() and path != destination and path.is_relative_to(destination):
            raise AssertionError('concurrent child realpath')
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', resolve_existing_root_only)
    assert delivery.restore(archive, destination)['files_verified'] == 3
