import { NextRequest, NextResponse } from "next/server";
import { isAdminAuthenticated } from "@/lib/admin-auth";

export async function GET(request: NextRequest) {
  if (!isAdminAuthenticated(request)) {
    return NextResponse.json({ detail: "Admin login required." }, { status: 401 });
  }
  const base = process.env.BACKEND_API_BASE_URL;
  const key = process.env.ADMIN_API_KEY;
  if (!base || !key) {
    return NextResponse.json({ detail: "Admin API configuration missing." }, { status: 500 });
  }
  try {
    const target = new URL(`${base.replace(/\/+$/, "")}/admin/token-usage`);
    request.nextUrl.searchParams.forEach((value, name) => target.searchParams.set(name, value));
    const response = await fetch(target, {
      headers: { "x-admin-key": key },
      cache: "no-store",
    });
    return NextResponse.json(await response.json(), { status: response.status });
  } catch {
    return NextResponse.json({ detail: "Unable to connect to backend." }, { status: 502 });
  }
}
