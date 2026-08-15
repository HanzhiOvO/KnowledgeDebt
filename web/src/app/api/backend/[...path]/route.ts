import "server-only";

const upstreamApiUrl = process.env.KNOWLEDGEDEBT_API_URL ?? "http://127.0.0.1:8123";

type ProxyContext = {
  params: Promise<{ path: string[] }>;
};

async function proxy(request: Request, context: ProxyContext) {
  const { path } = await context.params;
  const incomingUrl = new URL(request.url);
  const baseUrl = upstreamApiUrl.endsWith("/") ? upstreamApiUrl : `${upstreamApiUrl}/`;
  const targetUrl = new URL(path.map(encodeURIComponent).join("/"), baseUrl);
  targetUrl.search = incomingUrl.search;

  const headers = new Headers(request.headers);
  for (const name of ["host", "connection", "content-length", "accept-encoding"]) {
    headers.delete(name);
  }
  const accessToken = process.env.KNOWLEDGEDEBT_ACCESS_TOKEN;
  if (accessToken) {
    headers.set("authorization", `Bearer ${accessToken}`);
  }

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const response = await fetch(targetUrl, {
    method: request.method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
    cache: "no-store",
    redirect: "manual",
  });
  const responseHeaders = new Headers(response.headers);
  for (const name of ["content-encoding", "content-length", "transfer-encoding"]) {
    responseHeaders.delete(name);
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
