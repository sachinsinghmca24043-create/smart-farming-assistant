import { NextRequest, NextResponse } from "next/server";
import { isAdminAuthenticated } from "@/lib/admin-auth";

const BACKEND_URL =
  process.env.BACKEND_API_BASE_URL ||
  "http://127.0.0.1:8000/api/v1";

async function proxy(request: NextRequest, method: string) {
  if (!isAdminAuthenticated(request)) {
  return NextResponse.json(
    { detail: "Admin login required." },
    { status: 401 }
  );
}
  const adminKey = process.env.ADMIN_API_KEY;

  if (!adminKey) {
    return NextResponse.json(
      { detail: "Admin API key is not configured." },
      { status: 503 }
    );
  }

  try {
    const response = await fetch(`${BACKEND_URL}/admin/knowledge`, {
      method,
      headers: {
        "Content-Type": "application/json",
        "x-admin-key": adminKey,
      },
      ...(method !== "GET"
        ? { body: await request.text() }
        : {}),
      cache: "no-store",
    });

    const data = await response.json();

    return NextResponse.json(data, { status: response.status });
  } catch {
    return NextResponse.json(
      { detail: "Unable to connect to the backend." },
      { status: 502 }
    );
  }
}

export async function GET(request: NextRequest) {
  return proxy(request, "GET");
}

export async function POST(request: NextRequest) {
  return proxy(request, "POST");
}