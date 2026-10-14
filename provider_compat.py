"""Fix native request collisions and lossless fallback selection without copying URLs."""
import ast
import copy
import functools
import inspect
import json
import textwrap
import time


def audio_rank(song):
    from musicdl.musicdl import MusicClient
    return (str(song.ext).lower() in MusicClient.LOSSLESS_QUALITY_DEFINITIONS,
            int(song.file_size_bytes or 0))


class RequestKeywords(ast.NodeTransformer):
    def visit_Call(self, node):
        self.generic_visit(node)
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr in {'get', 'post', 'head', 'request'}
                and any(k.arg for k in node.keywords)
                and any(k.arg is None and isinstance(k.value, ast.Name)
                        and k.value.id == 'request_overrides' for k in node.keywords)):
            node.keywords = [ast.keyword(arg=None, value=ast.Dict(
                keys=[ast.Constant(k.arg) if k.arg else None for k in node.keywords],
                values=[k.value for k in node.keywords]))]
        return node


def prefer_lossless(tree):
    function = tree.body[0]
    loop = next(n for n in function.body if isinstance(n, ast.For)
                and isinstance(n.target, ast.Name) and n.target.id == 'parser_func')
    # Retain the native empty-song metadata and account/cookie gates.
    empty = copy.deepcopy(loop.body[0])
    replacement = ast.parse('''
for parser_func in parsers:
    candidate = None
    with suppress(Exception):
        candidate = parser_func(search_result, request_overrides)
    if candidate is not None and candidate.with_valid_download_url and candidate.ext in AudioLinkTester.VALID_AUDIO_EXTS:
        if not song_info_flac.with_valid_download_url or _musicdl_audio_rank(candidate) > _musicdl_audio_rank(song_info_flac):
            song_info_flac = candidate
        if _musicdl_audio_rank(candidate)[0]:
            break
''').body[0]
    ast.increment_lineno(replacement, loop.lineno - replacement.lineno)
    replacement.iter = loop.iter
    position = function.body.index(loop)
    function.body[position:position + 1] = [empty, replacement]


def qq_search_results(provider, response, search_meta, request_overrides, page_no, *, log):
    from musicdl.modules.sources.qq import QQMusicClientUtils, resp2json
    key = 'music.search.SearchCgiService.DoSearchForQQMusicMobile'

    def read(response, endpoint):
        response.raise_for_status()
        data = resp2json(resp=response)
        block = data.get(key) or {}
        songs = (block.get('data') or {}).get('body', {}).get('item_song') or []
        codes = (data.get('code'), block.get('code'))
        log('info', f'QQMusicClient {endpoint}搜索响应：第 {page_no} 页，状态码 {codes}，原始曲目 {len(songs)} 条', category='search')
        return songs, codes

    songs, codes = read(response, '签名' if provider.use_encrypted_endpoint else '普通')
    if songs:
        return songs
    # Reuse the native payload and signing implementation; leave account,
    # proxy, pagination and audio parsing settings intact.
    signed = not provider.use_encrypted_endpoint
    endpoint = '签名' if signed else '普通'
    log('warning', f'QQMusicClient 搜索接口未返回曲目，尝试原引擎{endpoint}接口', category='search')
    options = {**search_meta, **(request_overrides or {})}
    params = {**(search_meta.get('params') or {}), **(options.get('params') or {})}
    if signed:
        params['sign'] = QQMusicClientUtils.sign(json.loads(search_meta['data']))
    else:
        params.pop('sign', None)
    options['params'] = params
    response = provider.post(QQMusicClientUtils.enc_endpoint if signed else QQMusicClientUtils.endpoint, **options)
    songs, codes = read(response, endpoint)
    if not songs and any(code not in (None, 0) for code in codes):
        raise RuntimeError(f'QQ 搜索接口拒绝请求，状态码 {codes}')
    return songs


def qq_quality_missing(provider, response_data, quality, *, parser, log):
    data = response_data if isinstance(response_data, dict) else {}
    reason = data.get('message') or data.get('msg') or '未返回音频地址'
    if parser == '_parsewithvkeysapi':
        label = {14: '臻品母带 2.0', 13: '臻品空间音频', 12: '杜比全景声',
                 11: 'Hi-Res', 10: 'SQ 无损', 9: 'HQ 增强'}.get(quality, str(quality))
        quality = f'{label}（{quality}）'
    log('warning', f'{provider.source} 第三方解析接口 {parser} 音质 {quality} 不可用：'
                  f'接口错误码 {data.get("code", "未知")}，{str(reason)[:200]}；'
                  '继续尝试其他音质或解析接口。', category='search')


def recover_qq_quality_loop(tree):
    function = tree.body[0]
    function.body.insert(0, ast.parse('song_info = SongInfo(source=self.source)').body[0])
    loop = next(node for node in function.body if isinstance(node, ast.For)
                and isinstance(node.target, ast.Name) and node.target.id == 'music_quality')
    function.body.insert(function.body.index(loop) + 1, ast.parse('''
if not song_info.with_valid_download_url:
    return song_info
''').body[0])
    for node in ast.walk(function):
        if (isinstance(node, ast.If) and len(node.body) == 1 and isinstance(node.body[0], ast.Break)
                and any(isinstance(part, ast.NamedExpr) and isinstance(part.target, ast.Name)
                        and part.target.id == 'download_url' for part in ast.walk(node.test))):
            # Missing master audio must not hide the following lossless qualities.
            node.body = ast.parse('_musicdl_qq_quality_missing(self, download_result, music_quality)\ncontinue').body


def candidate_rows(provider, rows, songs):
    """Return native rows normally; candidate searches skip all audio parsers."""
    if not getattr(provider, '_musicdl_candidates', False):
        return rows
    from musicdl.modules import SongInfo
    for row in rows:
        qq = provider.source == 'QQMusicClient'
        album = row.get('album') or row.get('al') or {}
        artists = row.get('singer' if qq else 'artists') or row.get('ar') or []
        identifier = row.get('mid') or row.get('songmid') if qq else row.get('id')
        title = row.get('title') or row.get('songname') or row.get('name')
        if not identifier or not title:
            continue
        song = SongInfo(source=provider.source, identifier=str(identifier), song_name=title,
                        singers=', '.join(a.get('name', '') for a in artists if isinstance(a, dict)),
                        album=album.get('title') or album.get('name') or row.get('albumname') or '',
                        duration_s=int(row.get('interval') or 0) if qq else int((row.get('duration') or row.get('dt') or 0) / 1000),
                        cover_url=album.get('picUrl') or '',
                        raw_data={'search': copy.deepcopy(row), '_musicdl_candidate': True,
                                  '_musicdl_config': getattr(provider, '_musicdl_config', ''),
                                  '_musicdl_refresh': getattr(provider, '_musicdl_refresh', False),
                                  '_musicdl_started': getattr(provider, '_musicdl_started', time.time())})
        song.work_dir = provider.work_dir
        songs.append(song)
        if len(songs) >= provider.search_size_per_page:
            break
    return []


def lazy_search_methods(tree, namespace):
    """Compile the native per-track body as a resolver, keeping parser policy intact."""
    function = tree.body[0]
    loop = next(node for node in ast.walk(function) if isinstance(node, ast.For)
                and isinstance(node.target, ast.Tuple)
                and [getattr(n, 'id', '') for n in node.target.elts] == ['search_result_idx', 'search_result'])
    resolver = ast.parse('def _resolve_candidate(self, search_result, request_overrides=None):\n    pass').body[0]
    # QQ and Netease initialize the cookie-dependent lossless policy here.
    init = next(node for node in function.body if isinstance(node, ast.Assign)
                and 'lossless_quality_is_sufficient' in ast.unparse(node.targets[0]))
    body = [copy.deepcopy(node) for node in loop.body
            if not any(isinstance(n, ast.Break) or (isinstance(n, ast.Name) and n.id == 'progress')
                       for n in ast.walk(node))]
    resolver.body = [copy.deepcopy(init), *ast.parse('song_infos = []').body, *body,
                     *ast.parse('return song_infos').body]
    resolver.decorator_list = copy.deepcopy(function.decorator_list)
    tree.body.append(resolver)
    loop.iter.args[0] = ast.Call(func=ast.Name(id='_musicdl_candidate_rows', ctx=ast.Load()),
                                args=[ast.Name(id='self', ctx=ast.Load()), loop.iter.args[0],
                                      ast.Name(id='song_infos', ctx=ast.Load())], keywords=[])
    namespace['_musicdl_candidate_rows'] = candidate_rows


def compile_native(native, *, dispatcher=False, qq_search_log=None, qq_quality_log=None, lazy_search=False):
    native = inspect.unwrap(native)
    lines, start = inspect.getsourcelines(native)
    tree = ast.parse(textwrap.dedent(''.join(lines)))
    tree = RequestKeywords().visit(tree)
    if qq_quality_log is not None:
        recover_qq_quality_loop(tree)
    if dispatcher:
        prefer_lossless(tree)
    namespace = dict(native.__globals__, _musicdl_audio_rank=audio_rank)
    if qq_quality_log is not None:
        namespace['_musicdl_qq_quality_missing'] = functools.partial(qq_quality_missing, parser=native.__name__, log=qq_quality_log)
    if qq_search_log is not None:
        loop = next(n for n in ast.walk(tree) if isinstance(n, ast.For)
                    and isinstance(n.iter, ast.Call) and isinstance(n.iter.func, ast.Name)
                    and n.iter.func.id == 'enumerate' and 'item_song' in ast.unparse(n.iter))
        loop.iter.args[0] = ast.Call(func=ast.Name(id='_musicdl_qq_search_results', ctx=ast.Load()),
                                    args=[ast.Name(id=name, ctx=ast.Load()) for name in
                                          ('self', 'resp', 'search_meta', 'request_overrides', 'page_no')], keywords=[])
        namespace['_musicdl_qq_search_results'] = functools.partial(qq_search_results, log=qq_search_log)
    if lazy_search:
        lazy_search_methods(tree, namespace)
    tree = ast.increment_lineno(ast.fix_missing_locations(tree), start - 1)
    exec(compile(tree, inspect.getsourcefile(native), 'exec'), namespace)
    if lazy_search:
        return namespace[native.__name__], namespace['_resolve_candidate']
    return namespace[native.__name__]


def keep_quality_variants(self, song_infos=None):
    unique, seen = [], set()
    for song in song_infos or []:
        # An earlier MP3 must not hide a later FLAC with the same platform ID.
        key = (song.identifier, str(song.ext).lower(), song.file_size_bytes)
        if key not in seen:
            seen.add(key)
            unique.append(song)
    return unique


def observe(method, source, label, log):
    @functools.wraps(method)
    def parser(self, *args, **kwargs):
        try:
            song = method(self, *args, **kwargs)
        except Exception as error:
            log('warning', f'{source} 解析接口 {label} 失败：{type(error).__name__}: {error}', category='search')
            raise
        if song.with_valid_download_url:
            log('info', f'{source} 解析接口 {label} 返回音频：{song.ext}，{song.file_size_bytes} 字节', category='search')
        else:
            log('warning', f'{source} 解析接口 {label} 未返回可用音频地址', category='search')
        return song
    return parser


def install(log):
    from musicdl.modules.sources import kugou, kuwo, qq, netease
    status = {}
    for cls in (kugou.KugouMusicClient, kuwo.KuwoMusicClient,
                qq.QQMusicClient, netease.NeteaseMusicClient):
        try:
            if getattr(cls, '_musicdl_audio_compat', False):
                status[cls.source] = 'ok'
                continue
            # Compile first: changed upstream code cannot leave a half-patched source.
            methods = {}
            for name in vars(cls):
                if not (name.startswith('_parsewith') and name.endswith('api')):
                    continue
                quality_log = log if cls is qq.QQMusicClient and name in {'_parsewithvkeysapi', '_parsewithxcvtsapi'} else None
                methods[name] = observe(compile_native(getattr(cls, name), qq_quality_log=quality_log), cls.source, name, log)
            methods['_parsewiththirdpartapis'] = compile_native(cls._parsewiththirdpartapis, dispatcher=True)
            if cls in (qq.QQMusicClient, netease.NeteaseMusicClient):
                methods['_search'], methods['_resolve_candidate'] = compile_native(
                    cls._search, qq_search_log=log if cls is qq.QQMusicClient else None, lazy_search=True)
            methods['_removeduplicates'] = keep_quality_variants
            for name, method in methods.items():
                setattr(cls, name, method)
            cls._musicdl_audio_compat = True
            status[cls.source] = 'ok'
        except Exception as error:
            status[cls.source] = f'skip: {type(error).__name__}'
            log('error', f'{cls.source} 音质兼容修复未生效：{type(error).__name__}', category='search')
    return status
