export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-MusicDL-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const data = await response
    .json()
    .catch(() => ({ detail: "服务未返回有效数据，请检查服务状态" }));
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("musicdl-login"));
    throw new Error(data.detail || `请求失败 (${response.status})`);
  }
  return data as T;
}
