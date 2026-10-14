import pytest

from error_messages import explain_error
from telegram_local import LocalBotAPI


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('Telegram API error 413: file too big', '文件超过当前接口允许的上传大小'),
        ('Telegram API error 400: Bad Request: chat not found', '找不到接收会话'),
        ('Telegram API error 409: Conflict: terminated by other getUpdates request', '另一个程序或 webhook'),
        ('requests.exceptions.ConnectTimeout: timed out', '请求超时'),
        ('PermissionError: [Errno 13] Permission denied', '没有文件读写权限'),
    ],
)
def test_common_errors_have_plain_language_reasons(raw, expected):
    assert explain_error(raw).startswith(expected)


def test_unknown_errors_keep_the_original_diagnostic():
    assert explain_error('parser returned an unusual response') is None


def test_local_api_credentials_are_validated(monkeypatch):
    config = {'tg_local_api_id': '12345', 'tg_local_api_hash': 'a' * 32}
    assert LocalBotAPI.credentials(config) == ('12345', 'a' * 32)
    for key, value in [('tg_local_api_id', '0'), ('tg_local_api_hash', 'short')]:
        broken = {**config, key: value}
        with pytest.raises(ValueError):
            LocalBotAPI.credentials(broken)
    monkeypatch.setattr(LocalBotAPI, 'binary', staticmethod(lambda: None))
    with pytest.raises(ValueError, match='未找到官方'):
        LocalBotAPI(lambda *args, **kwargs: None).validate(config)
