'use strict';

const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync('static/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/);
assert(script, '找不到内联脚本');
new Function(script[1]);

const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
const duplicates = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
assert.deepStrictEqual(duplicates, [], `存在重复 DOM ID: ${duplicates.join(', ')}`);

const token = html.match(/const JOOX_TOKEN\s*=\s*'([^']*)'/);
assert(token && token[1] === '', '前端仍包含 JOOX Token');
assert(html.includes("'X-MusicDL-Request':'1'"), '写请求缺少安全请求头');
assert(html.includes('登录 musicdl'), '登录标题未中文化');
assert(html.includes('确定要退出登录吗？'), '登出确认未中文化');
assert(html.includes('let playGeneration=0'), '播放竞态保护缺失');

const backendBlock = html.split('musicdl 后端接管：以下函数覆盖上方同名实现')[1];
assert(backendBlock, '找不到后端接管代码块');
const persistedKeys = backendBlock.match(/const keys=\[([^\]]+)\]/g) || [];
assert(persistedKeys.every(line => !line.includes('backendId')), '仍在持久化 backendId');

console.log('FRONTEND_CHECK_OK');
