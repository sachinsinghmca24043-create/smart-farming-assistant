
import { NextRequest, NextResponse } from "next/server";
import { FARMER_COOKIE, getBackendApiUrl } from "@/lib/farmer-auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

async function proxyRequest(
  request: NextRequest,
  context: RouteContext
) {
  try {
    const token = request.cookies.get(FARMER_COOKIE)?.value;

    if (!token) {
      return NextResponse.json(
        { detail: "Authentication required." },
        { status: 401 }
      );
    }

    const { path } = await context.params;
    const backendPath = path.map(encodeURIComponent).join("/");
    const url = `${getBackendApiUrl()}/${backendPath}${request.nextUrl.search}`;

    const headers = new Headers({
      Authorization: `Bearer ${token}`,
      Accept: request.headers.get("accept") ?? "application/json",
    });

    const contentType = request.headers.get("content-type");
    if (contentType) {
      headers.set("Content-Type", contentType);
    }

    const method = request.method;
    const body =
      method === "GET" || method === "HEAD"
        ? undefined
        : await request.arrayBuffer();

    const backendResponse = await fetch(url, {
      method,
      headers,
      body,
      cache: "no-store",
    });

    const responseHeaders = new Headers();
    const responseContentType =
      backendResponse.headers.get("content-type");

    if (responseContentType) {
      responseHeaders.set("Content-Type", responseContentType);
    }

    return new NextResponse(await backendResponse.arrayBuffer(), {
      status: backendResponse.status,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json(
      { detail: "Unable to connect to the backend." },
      { status: 503 }
    );
  }
}

export const GET = proxyRequest;
export const POST = proxyRequest;
export const PUT = proxyRequest;
export const PATCH = proxyRequest;
export const DELETE = proxyRequest;
