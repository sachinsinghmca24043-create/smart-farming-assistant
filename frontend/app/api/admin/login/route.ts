import { NextRequest, NextResponse } from "next/server";
import { timingSafeEqual } from "node:crypto";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  try {
    const { password } = await request.json();

    const expectedPassword = process.env.ADMIN_LOGIN_PASSWORD;
    const sessionToken = process.env.ADMIN_SESSION_TOKEN;

    if (!expectedPassword || !sessionToken) {
      return NextResponse.json(
        { detail: "Admin authentication is not configured." },
        { status: 503 }
      );
    }

    const supplied = Buffer.from(
      typeof password === "string" ? password : ""
    );
    const expected = Buffer.from(expectedPassword);

    const valid =
      supplied.length === expected.length &&
      timingSafeEqual(supplied, expected);

    if (!valid) {
      return NextResponse.json(
        { detail: "Invalid admin password." },
        { status: 401 }
      );
    }

    const response = NextResponse.json({
      message: "Admin login successful.",
    });

    response.cookies.set("admin_session", sessionToken, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "strict",
      path: "/",
      maxAge: 8 * 60 * 60,
    });

    return response;
  } catch {
    return NextResponse.json(
      { detail: "Invalid login request." },
      { status: 400 }
    );
  }
}