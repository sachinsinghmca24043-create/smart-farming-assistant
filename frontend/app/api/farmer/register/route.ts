import { NextRequest, NextResponse } from "next/server";
import { getBackendApiUrl } from "@/lib/farmer-auth";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    const backendResponse = await fetch(
      `${getBackendApiUrl()}/auth/register`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        cache: "no-store",
      }
    );

    const data = await backendResponse.json();

    return NextResponse.json(data, {
      status: backendResponse.status,
    });
  } catch {
    return NextResponse.json(
      { detail: "Unable to connect to the registration service." },
      { status: 503 }
    );
  }
}