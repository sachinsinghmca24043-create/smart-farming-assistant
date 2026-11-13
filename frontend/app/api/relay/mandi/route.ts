import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const preferredRegion = "bom1";

export async function GET(req: NextRequest) {
  const upstream = new URL(
    "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"
  );
  req.nextUrl.searchParams.forEach((value, key) => {
    upstream.searchParams.set(key, value);
  });

  try {
    const res = await fetch(upstream.toString(), {
      headers: {
        "User-Agent": "SmartFarmingAssistant/1.0",
        Accept: "application/json",
      },
      cache: "no-store",
    });
    const text = await res.text();
    return new Next/Response(text, {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return NextResponse.json(
      { error: "Upstream mandi relay failed", detail: String(err) },
      { status: 502 }
    );
  }
}
