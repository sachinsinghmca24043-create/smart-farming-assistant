import { NextRequest, NextResponse } from "next/server";
import {
  FARMER_COOKIE,
  getBackendApiUrl,
} from "@/lib/farmer-auth";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    const backendResponse = await fetch(
      `${getBackendApiUrl()}/auth/login`,
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

    if (!backendResponse.ok) {
      return NextResponse.json(data, {
        status: backendResponse.status,
      });
    }

    if (
      typeof data.access_token !== "string" ||
      !data.access_token ||
      !data.user
    ) {
      return NextResponse.json(
        { detail: "Invalid authentication response." },
        { status: 502 }
      );
    }

    const response = NextResponse.json({
      status: "success",
      user: data.user,
      expires_at: data.expires_at,
    });

    response.cookies.set(FARMER_COOKIE, data.access_token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "strict",
      path: "/",
      maxAge: 24 * 60 * 60,
    });

    return response;
  } catch {
    return NextResponse.json(
      { detail: "Unable to connect to the login service." },
      { status: 503 }
    );
  }
}