Deno.serve(async (request: Request) => {
  const url = new URL(request.url);

  if (request.method === "OPTIONS") {
    return new Response(null, {
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET",
        "Access-Control-Allow-Headers": "*",
      },
    });
  }

  const target = url.searchParams.get("url");
  if (!target) {
    return new Response(JSON.stringify({ error: "missing ?url= parameter" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  if (!target.includes("eastmoney.com")) {
    return new Response(JSON.stringify({ error: "only eastmoney.com allowed" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const resp = await fetch(target, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "*/*",
      },
    });

    const data = await resp.text();
    return new Response(data, {
      status: resp.status,
      headers: {
        "Content-Type": resp.headers.get("Content-Type") || "application/json; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
      },
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: (e as Error).message }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
});
