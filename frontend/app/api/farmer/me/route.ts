import { NextRequest, NextResponse } from "next/server";
import {
  FARMER_COOKIE,
  getBackendApiUrl,
} from "@/lib/farmer-auth";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  try {
    const token = request.cookies.get(FARMER_COOKIE)?.value;

    if (!token) {
      return NextResponse.json(
        { authenticated: false },
        { status: 401 }
      );
    }

    const backendResponse = await fetch(
      `${getBackendApiUrl()}/auth/me`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
        },
        cache: "no-store",
      }
    );

    const data = await backendResponse.json();

    return NextResponse.json(data, {
      status: backendResponse.status,
    });
  } catch {
    return NextResponse.json(
      { detail: "Unable to verify farmer session." },
      { status: 503 }
    );
  }
}