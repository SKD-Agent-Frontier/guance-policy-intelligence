"""校验Release分卷，恢复到新目录；密钥只从接收方本地配置读取。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import zipfile

PORTABLE_ROOT = '__RAG_DELIVERY_ROOT__'
EMBEDDING_URL = '__CONFIGURE_EMBEDDING_BASE_URL__'


def io_path(path):
    # SHA目录在Windows上可能超过MAX_PATH；仅调整文件API路径，不改来源ID。
    value = os.path.abspath(os.fspath(path))
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        value = '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    return Path(value)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def checksum(path):
    with io_path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_path(root, value, *, resolve=True):
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or '..' in path.parts or '\\' in value or ':' in value:
        raise ValueError('unsafe archive path')
    result = root.joinpath(*path.parts)
    if resolve:
        result = result.resolve()
    if not result.is_relative_to(root):
        raise ValueError('archive path escaped destination')
    return result


def assemble(manifest_path, parts_directory=None):
    manifest_path = Path(manifest_path).resolve()
    manifest = read(manifest_path)
    root = Path(parts_directory or manifest_path.parent).resolve()
    for part in manifest['parts']:
        path = safe_path(root, part['name'])
        if path.stat().st_size != part['bytes'] or checksum(path) != part['sha256']:
            raise ValueError('release part checksum mismatch: ' + part['name'])
    archive = safe_path(root, manifest['archive']['name'])
    if not archive.exists():
        temporary = archive.with_name(archive.name + '.assembling')
        with temporary.open('xb') as output:
            for part in manifest['parts']:
                with safe_path(root, part['name']).open('rb') as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
        temporary.replace(archive)
    if archive.stat().st_size != manifest['archive']['bytes'] or checksum(archive) != manifest['archive']['sha256']:
        raise ValueError('assembled archive checksum mismatch')
    return archive, manifest


def rebase(destination):
    database = destination / '.runtime/local/metadata.sqlite3'
    with sqlite3.connect(database) as db:
        for rid, raw in db.execute('SELECT id,manifest FROM releases').fetchall():
            manifest = json.loads(raw)
            for key in ('prepared_path', 'media_path'):
                relative = PurePosixPath(manifest[key]).relative_to(PORTABLE_ROOT)
                manifest[key] = str(destination.joinpath(*relative.parts))
            db.execute('UPDATE releases SET manifest=? WHERE id=?',
                       (json.dumps(manifest, ensure_ascii=False), rid))
    for path in (destination / 'outputs').glob('local_generation_*/manifest.json'):
        manifest = read(path)
        for key in ('prepared_path', 'media_path'):
            relative = PurePosixPath(manifest[key]).relative_to(PORTABLE_ROOT)
            manifest[key] = str(destination.joinpath(*relative.parts))
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def configure(destination, secrets_path):
    # 私密配置不进入分卷；接收方可在初次恢复或后续单独绑定模型服务。
    from filelock import FileLock
    private = read(secrets_path)
    url = private.get('embedding_base_url')
    if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
        raise ValueError('local embedding_base_url is required')
    with FileLock(str(destination / '.runtime/project.lock'), timeout=0):
        target = destination / 'secrets.local.json'
        if Path(secrets_path).resolve() != target.resolve():
            if target.exists():
                raise ValueError('destination already has private settings; edit them explicitly')
            shutil.copyfile(secrets_path, target)
        with sqlite3.connect(destination / '.runtime/local/metadata.sqlite3') as db:
            for rid, raw in db.execute('SELECT id,manifest FROM releases').fetchall():
                manifest = json.loads(raw)
                if manifest['profile']['service_url'] == EMBEDDING_URL:
                    manifest['profile']['service_url'] = url
                    db.execute('UPDATE releases SET manifest=? WHERE id=?',
                               (json.dumps(manifest, ensure_ascii=False), rid))
        for path in (destination / 'outputs').glob('local_generation_*/manifest.json'):
            manifest = read(path)
            if manifest['profile']['service_url'] == EMBEDDING_URL:
                manifest['profile']['service_url'] = url
                path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def restore(archive, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('restore destination must not exist')
    with zipfile.ZipFile(archive) as package:
        manifest = json.loads(package.read('backup_manifest.json'))
        if manifest['project_root'] != PORTABLE_ROOT:
            raise ValueError('unsupported delivery root')
        entries = manifest['files']
        names = [row['path'] for row in entries]
        if len(set(names)) != len(names) or set(package.namelist()) != set(names) | {'backup_manifest.json'}:
            raise ValueError('archive member list mismatch')
        for name in names:
            safe_path(destination, name, resolve=False)
        destination.mkdir(parents=True)

        def one(entry):
            # 目标必须全新，且我们只创建目录及普通文件（不恢复ZIP符号链接）。
            # 先完成词法越界校验，再并发写入，避免对正在创建的长路径反复realpath。
            path = io_path(safe_path(destination, entry['path'], resolve=False))
            path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with package.open(entry['path']) as source, path.open('xb') as output:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != entry['sha256']:
                raise ValueError('extracted file checksum mismatch: ' + entry['path'])
        with ThreadPoolExecutor(max_workers=8) as executor:
            # map持续消费，不保留全部文件内容；每个文件最多1MiB读取缓冲。
            for _ in executor.map(one, entries):
                pass
    rebase(destination)
    return {'generation_id': manifest['generation_id'], 'files_verified': len(entries)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--parts-directory', type=Path)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--secrets', type=Path)
    parser.add_argument('--configure-only', action='store_true')
    args = parser.parse_args()
    destination = args.destination.resolve()
    if args.configure_only:
        if not args.secrets:
            parser.error('--configure-only requires --secrets')
        result = {'configured': True}
    else:
        if not args.manifest:
            parser.error('--manifest is required for restore')
        archive, _ = assemble(args.manifest, args.parts_directory)
        result = restore(archive, destination)
    if args.secrets:
        configure(destination, args.secrets)
    # 不输出配置内容、服务URL、鉴权或原作者路径。
    print(json.dumps({'passed': True, **result, 'destination': str(destination)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
