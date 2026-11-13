import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const preferredRegion = "bom1";

export async function GET(req: NextRequest) {
  const upstream = new URL("https://api.open-meteo.com/v1/forecast");
  req.nextUrl.searchParams.forEach((value, key) => {
    upstream.searchParams.set(key, value);
  });

  try {
    const res = await fetch(upstream.toString(), {
      headers: {
        "User-Agent": "SmartKrishiFarmingAssistant/1.0",
        Accept: "application/json",
      },
      cache: "no-store",
    });
    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return NextResponse.json(
      { error: "Upstream weather relay failed", detail: String(err) },
      { status: 502 }
    );
  }
}
