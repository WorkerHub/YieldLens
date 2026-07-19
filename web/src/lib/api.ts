const BASE_URL = "/api";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...((options.headers as Record<string, string>) || {}),
    },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: "Unknown error" }));
    throw new ApiError(response.status, body.error || "Request failed");
  }

  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return undefined as T;
  }

  return response.json();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
};

export { ApiError };
