// Cloudflare Pages Function — handles /pdf
//
// Proxies a ChurchSuite CDN PDF so the browser only ever makes a same-origin
// request (no CORS / extension interference). Locked to a single host so it
// can't be abused as an open proxy (no SSRF to arbitrary or internal hosts).

const ALLOWED_HOST = "cdn.churchsuite.com";

export async function onRequest(context) {
  const url = new URL(context.request.url);
  const target = url.searchParams.get("url");
  if (!target) return new Response("Missing url", { status: 400 });

  let t;
  try {
    t = new URL(target);
  } catch {
    return new Response("Bad url", { status: 400 });
  }
  if (t.protocol !== "https:" || t.hostname !== ALLOWED_HOST) {
    return new Response("Forbidden host", { status: 403 });
  }

  const upstream = await fetch(t.toString(), {
    method: "GET",
    cf: { cacheTtl: 300, cacheEverything: true },
  });
  if (!upstream.ok) return new Response("Upstream error", { status: 502 });

  const headers = new Headers();
  headers.set("Content-Type", upstream.headers.get("Content-Type") || "application/pdf");
  headers.set("Cache-Control", "public, max-age=300");
  return new Response(upstream.body, { status: 200, headers });
}
