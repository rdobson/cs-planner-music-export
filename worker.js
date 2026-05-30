// Cloudflare Worker: serves the static site and proxies ChurchSuite PDFs.
//
// The browser only ever talks to this same origin, so there is no cross-origin
// request for a privacy/ad-block extension or CORS policy to interfere with.
// The Worker fetches the PDF from ChurchSuite's CDN server-side and streams it
// back. The proxy is locked to a single host so it can't be abused as an open
// proxy (no SSRF to arbitrary or internal addresses).

const ALLOWED_HOST = "cdn.churchsuite.com";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/pdf") {
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
      if (!upstream.ok) {
        return new Response("Upstream error", { status: 502 });
      }

      const headers = new Headers();
      headers.set("Content-Type", upstream.headers.get("Content-Type") || "application/pdf");
      headers.set("Cache-Control", "public, max-age=300");
      return new Response(upstream.body, { status: 200, headers });
    }

    // Everything else: serve the static assets (index.html, etc.)
    return env.ASSETS.fetch(request);
  },
};
