const DEFAULT_TTL_SECONDS = 300;

export async function getCached<T>(kv: KVNamespace, key: string): Promise<T | null> {
  const raw = await kv.get(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export async function setCached<T>(
  kv: KVNamespace,
  key: string,
  data: T,
  ttlSeconds: number = DEFAULT_TTL_SECONDS,
): Promise<void> {
  await kv.put(key, JSON.stringify(data), { expirationTtl: ttlSeconds });
}

export function rankingCacheKey(params: Record<string, unknown>): string {
  const sorted = Object.keys(params)
    .sort()
    .reduce<Record<string, unknown>>((acc, k) => {
      if (params[k] !== undefined) acc[k] = params[k];
      return acc;
    }, {});
  return `ranking:${JSON.stringify(sorted)}`;
}
