export const publicApiUrl =
  process.env.NEXT_PUBLIC_KNOWLEDGEDEBT_API_URL ?? "/api/backend";

export async function mutate<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${publicApiUrl}${path}`, init);
  const body = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) {
    throw new Error(body.detail ?? `请求失败 (${response.status})`);
  }
  return body as T;
}
