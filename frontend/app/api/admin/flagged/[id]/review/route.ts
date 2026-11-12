import { NextRequest, NextResponse } from "next/server";
import { isAdminAuthenticated } from "@/lib/admin-auth";

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ id: string }> }
) {
  if (!isAdminAuthenticated(request)) {
    return NextResponse.json(
      { detail: "Admin login required." },
      { status: 401 }
    );
  }

  const base = process.env.BACKEND_API_BASE_URL;
  const key = process.env.ADMIN_API_KEY;

  if (!base || !key) {
    return NextResponse.json(
      { detail: "Admin API configuration missing." },
      { status: 500 }
    );
  }

  try {
    const { id } = await context.params;
    const body = await request.json();

    const response = await fetch(
      `${base}/admin/flagged/${encodeURIComponent(id)}/review`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-admin-key": key,
        },
        body: JSON.stringify(body),
        cache: "no-store",
      }
    );

    const data = await response.json();

    return NextResponse.json(data, {
      status: response.status,
    });
  } catch {
    return NextResponse.json(
      { detail: "Unable to update review item." },
      { status: 502 }
    );
  }
}