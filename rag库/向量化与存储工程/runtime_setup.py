"""下载官方便携二进制并在工程内运行真实数据库；不修复或重置Docker。

下载与数据均放在.gitignore排除的.runtime目录。凭证不写入命令参数或日志；
PostgreSQL通过临时pwfile初始化，Qdrant通过私密YAML配置仅监听回环地址。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import tempfile
import ctypes
import urllib.request
import urllib.error
from http.client import HTTPException
import zipfile

PROJECT=Path(__file__).resolve().parent
RUNTIME=PROJECT/'.runtime'


def ascii_root(project=PROJECT):
    """Windows原生PG的bootstrap会把中文路径按ANSI送进UTF8 SQL，故使用ASCII入口。

    先尝试已有8.3短路径；若没有，则在用户临时目录创建指向本工程的目录联接。
    数据仍在本工程，不复制或移动语料，不修改系统区域设置，也不覆盖既有目录。
    """
    project=Path(project).resolve()
    allowed={PROJECT.resolve(),(PROJECT.parent/'向量化与存储工程（开源组件版）').resolve()}
    if project not in allowed:raise ValueError('unmanaged native runtime project')
    if os.name!='nt':return project
    buffer=ctypes.create_unicode_buffer(32768)
    function=ctypes.windll.kernel32.GetShortPathNameW
    function.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint32]
    function.restype=ctypes.c_uint32
    length=function(str(project),buffer,len(buffer))
    if length and buffer.value.isascii():return Path(buffer.value)
    suffix=hashlib.sha256(str(project).encode()).hexdigest()[:12]
    alias=Path(tempfile.gettempdir())/('guance-rag-'+suffix)
    if not alias.exists():
        environment={**os.environ,'GUANCE_ASCII_ALIAS':str(alias),'GUANCE_ASCII_TARGET':str(project)}
        subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
            'New-Item -ItemType Junction -Path $env:GUANCE_ASCII_ALIAS -Target $env:GUANCE_ASCII_TARGET -ErrorAction Stop | Out-Null'],
            env=environment,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if alias.resolve()!=project or not str(alias).isascii():
        raise ValueError('ASCII runtime alias does not point to this project')
    write_json(RUNTIME/('ascii-path-'+suffix+'.json'),{'alias':str(alias),'target':str(project),'kind':'directory_junction','data_copied':False})
    return alias


def native_path(path):
    value=str(path).removeprefix('\\\\?\\')
    absolute=Path(value).resolve()
    project=next((p for p in [PROJECT.resolve(),(PROJECT.parent/'向量化与存储工程（开源组件版）').resolve()] if absolute.is_relative_to(p)),None)
    if project is None:raise ValueError('native path outside managed projects')
    result=ascii_root(project)/absolute.relative_to(project)
    if not str(result).isascii():raise ValueError('native PostgreSQL path must be ASCII')
    return result


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def download(url, target, expected_digest=None, fallback_url=None):
    """流式下载并记录SHA-256；若官方发布提供digest则必须逐字核对。"""
    endpoint=url
    channel='existing_cache'
    if not target.exists():
        temporary=target.with_suffix(target.suffix+'.download')
        modes=[('direct',urllib.request.build_opener(urllib.request.ProxyHandler({}))),
               ('system_configured_proxy',urllib.request.build_opener())]
        last=None
        for endpoint in ([url,fallback_url] if fallback_url else [url]):
            for channel,opener in modes:
                try:
                    request=urllib.request.Request(endpoint,headers={'User-Agent':'guance-rag-builder','Accept':'application/octet-stream'})
                    with opener.open(request,timeout=60) as response, temporary.open('wb') as output:
                        length=response.headers.get('Content-Length');written=0
                        while block:=response.read(1024*1024):
                            output.write(block);written+=len(block)
                        output.flush();os.fsync(output.fileno())
                    if length and written!=int(length):
                        raise OSError('download byte length mismatch')
                    with temporary.open('rb') as stream:
                        checksum=hashlib.file_digest(stream,'sha256').hexdigest()
                    if expected_digest and checksum!=expected_digest.removeprefix('sha256:'):
                        raise OSError('download does not match official SHA-256')
                    os.replace(temporary,target)
                    last=None
                    break
                except (OSError,urllib.error.URLError,HTTPException) as error:
                    last=error
                    print('official download retry',target.name,channel,type(error).__name__,flush=True)
            if last is None:break
        if last is not None:raise last
    digest=hashlib.file_digest(target.open('rb'),'sha256').hexdigest()
    if expected_digest and digest != expected_digest.removeprefix('sha256:'):
        raise ValueError('official download digest mismatch: '+target.name)
    return {'url':url,'download_endpoint':endpoint,'network_channel':channel,'sha256':digest,'bytes':target.stat().st_size,'official_digest':expected_digest}


def extract_archive(archive,directory,prefixes=None):
    marker=directory/'.complete'
    if marker.exists():return
    directory.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if prefixes and not info.filename.startswith(prefixes):continue
            destination=(directory/info.filename).resolve()
            if not destination.is_relative_to(directory.resolve()):raise ValueError('unsafe archive path')
            z.extract(info,directory)
    marker.write_text('extraction completed\n',encoding='utf-8')


def install_postgres():
    """两种数据库可独立安装，某一官方下载短时不可用不阻挡另一项验证。"""
    RUNTIME.mkdir(exist_ok=True)
    p=download('https://get.enterprisedb.com/postgresql/postgresql-16.13-1-windows-x64-binaries.zip',RUNTIME/'postgresql-16.13.zip')
    extract_archive(RUNTIME/'postgresql-16.13.zip',RUNTIME/'postgres-bin',('pgsql/bin/','pgsql/lib/','pgsql/share/'))
    profile=json.loads((PROJECT/'runtime_profile.json').read_text(encoding='utf-8')) if (PROJECT/'runtime_profile.json').exists() else {}
    profile.update(postgresql={'version':'16.13',**p},installation='native_portable_windows',listen='loopback_only')
    write_json(PROJECT/'runtime_profile.json',profile)
    print('PostgreSQL portable binaries installed',flush=True)


def install():
    RUNTIME.mkdir(exist_ok=True)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request('https://api.github.com/repos/qdrant/qdrant/releases/tags/v1.19.2',headers={'User-Agent':'guance-rag-builder'}),timeout=30) as response:
        release=json.load(response)
    asset=next(x for x in release['assets'] if x['name']=='qdrant-x86_64-pc-windows-msvc.zip')
    q=download(asset['browser_download_url'],RUNTIME/'qdrant-1.19.2.zip',asset.get('digest'),asset['url'])
    p=download('https://get.enterprisedb.com/postgresql/postgresql-16.13-1-windows-x64-binaries.zip',RUNTIME/'postgresql-16.13.zip')
    for archive,directory,prefixes in [(RUNTIME/'qdrant-1.19.2.zip',RUNTIME/'qdrant-bin',None),
                                      (RUNTIME/'postgresql-16.13.zip',RUNTIME/'postgres-bin',('pgsql/bin/','pgsql/lib/','pgsql/share/'))]:
        extract_archive(archive,directory,prefixes)
    write_json(PROJECT/'runtime_profile.json',{'postgresql':{'version':'16.13',**p},'qdrant':{'version':'1.19.2',**q},'installation':'native_portable_windows','listen':'loopback_only'})
    print('official portable database binaries installed',flush=True)


def start_postgres():
    """幂等启动本工程专用端口；不停止或覆盖机器上其他数据库。"""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    config=json.loads((PROJECT/'secrets.local.json').read_text(encoding='utf-8'))
    connection=conninfo_to_dict(config['postgres_dsn'])
    native_runtime=ascii_root()/'.runtime'
    bindir=native_runtime/'postgres-bin/pgsql/bin'
    data=native_runtime/'postgres-data'
    if not data.exists():
        password_file=native_runtime/'postgres-password.local.env'
        password_file.write_text(connection['password']+'\n',encoding='utf-8')
        try:
            subprocess.run([str(bindir/'initdb.exe'),'-D',str(data),'-U',connection['user'],'--encoding=UTF8','--locale=C','--auth-host=scram-sha-256','--auth-local=scram-sha-256','--pwfile='+str(password_file)],check=True,stdout=subprocess.DEVNULL,cwd=native_runtime)
        finally:
            password_file.unlink(missing_ok=True)
        (data/'postgresql.auto.conf').write_text("listen_addresses = '127.0.0.1'\nport = 15432\nmax_connections = 30\nshared_buffers = '128MB'\n",encoding='utf-8')
    status=subprocess.run([str(bindir/'pg_ctl.exe'),'status','-D',str(data)],capture_output=True)
    if status.returncode:
        subprocess.run([str(bindir/'pg_ctl.exe'),'start','-D',str(data),'-l',str(native_runtime/'postgres.log'),'-w'],check=True,stdout=subprocess.DEVNULL,cwd=native_runtime)
    admin={**connection,'dbname':'postgres'}
    with psycopg.connect(**admin,autocommit=True) as conn:
        if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(connection['dbname'],)).fetchone():
            from psycopg import sql
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(connection['dbname'])))
    with psycopg.connect(config['postgres_dsn']) as conn:
        actual=conn.execute('SELECT version()').fetchone()[0]
    write_json(RUNTIME/'postgres-status.json',{'version':actual,'host':'127.0.0.1','port':15432,'database':connection['dbname']})
    print('PostgreSQL listening on project loopback port',flush=True)


def start_qdrant():
    config=json.loads((PROJECT/'secrets.local.json').read_text(encoding='utf-8'))
    qconfig=RUNTIME/'qdrant.local.yaml'
    # Windows的payload gridstore会在长的绝对路径下触发MAX_PATH。
    # 以已核验的ASCII入口作为cwd，并使用相对数据路径；实际数据不移动。
    native_runtime=ascii_root()/'.runtime'
    # 官方storage.temp_path也显式缩短，避免恢复临时目录再叠加collection/segment名称。
    qconfig.write_text('storage:\n  storage_path: ./qdrant-data\n  snapshots_path: ./snapshots\n  temp_path: ./t\nservice:\n  host: 127.0.0.1\n  http_port: 16333\n  grpc_port: 16334\n  api_key: '+json.dumps(config['qdrant_api_key'])+'\ntelemetry_disabled: true\n',encoding='utf-8')
    import httpx
    with httpx.Client(trust_env=False,timeout=2) as client:
        try:
            response=client.get(config['qdrant_url']+'/',headers={'api-key':config['qdrant_api_key']})
            response.raise_for_status()
        except (httpx.HTTPError,ValueError):
            executable=next((RUNTIME/'qdrant-bin').rglob('qdrant.exe'))
            # CREATE_NO_WINDOW不会弹出终端；持久化数据库进程及日志均在本工程内。
            with (RUNTIME/'qdrant.log').open('ab') as log:
                process=subprocess.Popen([str(native_path(executable)),'--config-path',str(native_path(qconfig))],cwd=native_runtime,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            write_json(RUNTIME/'processes.local.json',{'qdrant_pid':process.pid})
            for _ in range(30):
                try:
                    response=client.get(config['qdrant_url']+'/',headers={'api-key':config['qdrant_api_key']})
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    time.sleep(.5)
            else:
                raise RuntimeError('Qdrant did not start; inspect private runtime log')
    write_json(RUNTIME/'qdrant-status.json',{'response':response.json(),'host':'127.0.0.1','port':16333})
    print('Qdrant listening on project loopback port',flush=True)


def start():
    start_postgres();start_qdrant()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['install','install-postgres','start','start-postgres','start-qdrant'])
    args=parser.parse_args()
    globals()[args.command.replace('-','_')]()
