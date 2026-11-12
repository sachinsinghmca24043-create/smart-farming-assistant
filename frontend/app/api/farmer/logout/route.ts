import { NextRequest, NextResponse } from "next/server";
import {
  FARMER_COOKIE,
  getBackendApiUrl,
} from "@/lib/farmer-auth";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const token = request.cookies.get(FARMER_COOKIE)?.value;

  const response = NextResponse.json({
    status: "success",
    message: "Logged out successfully.",
  });

  if (token) {
    try {
      await fetch(`${getBackendApiUrl()}/auth/logout`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
        cache: "no-store",
      });
    } catch {
      // Clear the browser cookie even if the backend is unavailable.
    }
  }

  response.cookies.set(FARMER_COOKIE, "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: 0,
  });

  return response;
}