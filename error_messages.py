"""Plain-language explanations; callers retain redacted diagnostics separately."""
import re


def explain_error(error):
    text = str(error).lower().replace('_', ' ')
    if re.search(r'\b413\b|file (?:is )?too (?:big|large)|(?:entity|payload) too large', text):
        return '文件超过当前接口允许的上传大小，无法直接发送。可使用下载链接，或启用内置 Telegram 本地服务后重试。'
    if 'bot was blocked' in text:
        return '机器人被用户屏蔽，请在 Telegram 中解除屏蔽后重试。'
    if 'chat not found' in text:
        return '找不到接收会话，请先私聊机器人发送 /start，并检查用户 ID。'
    if 'getupdates' in text and ('conflict' in text or '409' in text):
        return '另一个程序或 webhook 正在接收此机器人的消息。请关闭重复实例或移除 webhook。'
    if 'api id' in text and ('invalid' in text or 'hash' in text and 'invalid' in text):
        return 'Telegram API ID 或 API Hash 无效，请核对 my.telegram.org 中的应用凭据。'
    if re.search(r'\b401\b|unauthorized|\b40001\b|\b40014\b', text):
        return '身份验证失败，Token 或应用密钥无效、已失效，请检查渠道凭据。'
    if re.search(r'\b429\b|too many requests|flood wait', text):
        retry = re.search(r'retry after[ :=]+(\d+)', text)
        return f'请求过于频繁，请在 {retry[1]} 秒后重试。' if retry else '请求过于频繁，平台暂时限流，请稍后重试。'
    if 'certificate verify failed' in text or 'sslerror' in text:
        return '安全连接验证失败，请检查系统时间、证书或代理设置。'
    if 'proxyerror' in text or 'cannot connect to proxy' in text:
        return '无法连接代理，请检查代理地址、端口和代理服务是否运行。'
    if 'getaddrinfo failed' in text or 'name resolution' in text or 'nameresolutionerror' in text:
        return '域名解析失败，请检查网络和 DNS 设置。'
    if 'timeout' in text or 'timed out' in text:
        return '请求超时，对方服务未及时响应，请检查网络或代理后重试。'
    if 'connection refused' in text or 'winerror 10061' in text:
        return '目标服务拒绝连接，请检查服务是否启动以及地址、端口是否正确。'
    if 'connection reset' in text or 'connectionerror' in text or 'remotedisconnected' in text:
        return '网络连接中断，请检查网络或代理后重试。'
    if 'no space left' in text or 'disk full' in text:
        return '磁盘空间不足，请清理下载目录或数据目录所在磁盘。'
    if 'permission denied' in text or 'permissionerror' in text:
        return '没有文件读写权限，请检查挂载目录权限和运行用户。'
    if re.search(r'\b403\b|forbidden|\b60020\b', text):
        return '访问被拒绝，请检查账号权限、Cookie、企业可信 IP 或平台限制。'
    if re.search(r'\b50[234]\b|bad gateway|service unavailable', text):
        return '远端服务暂时不可用，请稍后重试或更换音源。'
    return None
